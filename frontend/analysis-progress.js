/* The seven business modules are a view over persisted pipeline traces and outputs. */
(function (global) {
  const modules = [
    { key: 'materials', short: '材料预检', title: '材料准备与预检', subtitle: '上传、可读性、分类复核' },
    { key: 'filing', short: '备案核验', title: '备案证识别与禁投核验', subtitle: '备案字段、原文证据、禁投规则' },
    { key: 'installation', short: '组图安装', title: '组图安装类型判定', subtitle: '整单安装类型、承保范围' },
    { key: 'single', short: '单图检查', title: '单图专项检查', subtitle: '逐图识别、文字水印、逐图规则' },
    { key: 'completeness', short: '前置核验', title: '材料完整性与位置核验', subtitle: '适用检查项、位置一致性、证据缺口' },
    { key: 'catastrophe', short: '抗灾筛查', title: '抗灾参数与能力筛查', subtitle: '组图参数、外部数据、风雪比较' },
    { key: 'decision', short: '整单建议', title: '整单建议', subtitle: '规则依据、风险提示、最终建议' },
  ];
  const stateNames = {
    done: '已完成', stopped: '提前拒保', attention: '需处理', partial: '部分完成',
    failed: '执行失败', skipped: '已跳过', active: '运行中', waiting: '待运行', unrecorded: '历史未记录',
  };
  const stepNames = {
    material_parse: '材料预检', ocr: '文字与水印提取', filing_rules: '备案禁投规则',
    installation_group: '组图安装类型识别', installation_rules: '安装类型规则',
    vision: '单图风险识别', rule_engine: '规则核验', completeness: '材料完整性判定',
    geometry_group: '抗灾参数组图估计', equipment_inventory: '设备清单提取',
    component_lookup: '组件参数查询', weather_lookup: '历史气象查询',
    catastrophe_assessment: '抗灾量化筛查', front_load_screening:'风雪风险量化', decision: '整单建议',
  };
  const sceneNames = {
    building_roof: '屋顶式', carport_roof: '车棚顶式', ground_mount: '地面支架式',
    floating: '水面漂浮式', other_canopy: '其他棚架式', building_facade: '建筑立面式',
    other_known: '其他已知类型', unknown: '未知',
  };
  const decisionNames = {
    accept: '建议承保', recommend_reject: '建议拒保', surcharge: '建议承保（加费）',
    conditional_accept: '建议附条件承保', request_more: '需补充材料', manual_review: '需人工复核',
  };
  const actionNames = {
    continue: '继续', recommend_reject: '建议拒保', request_more: '需补充材料', manual_review: '需人工复核',
  };
  const materialActionNames = {
    pass: '通过', warning: '风险提示', surcharge: '建议加费', recommend_reject: '建议拒保',
    conditional: '附条件承保', request_more: '需补充材料', not_applicable: '不适用',
  };
  const fieldNames = {
    document_title: '文件标题', document_type: '文件类型', project_name: '项目名称',
    project_type: '项目类型', construction_content: '建设内容', project_entity: '项目单位',
    site_address: '建设地点', document_number: '备案编号', issue_date: '备案日期',
    watermark_present: '拍摄水印', watermark_longitude: '水印经度', watermark_latitude: '水印纬度',
    watermark_coordinate_system: '水印坐标系', watermark_address: '水印地址',
    watermark_date: '水印日期', watermark_time: '水印时间',
  };
  const filingCategories = new Set(['filing_certificate', 'project_document']);
  const node = (tag, className, value) => {
    const element = document.createElement(tag);
    if (className) element.className = className;
    if (value !== undefined) element.textContent = String(value);
    return element;
  };
  const value = input => input === null || input === undefined || input === '' ? '未提供' : String(input);

  function traceGroups(result) {
    const groups = modules.map(() => []);
    const traces = result?.processing_trace || [];
    const filingRuleIndex = traces.findIndex(trace => trace.step === 'filing_rules');
    traces.forEach((trace, index) => {
      let group;
      if (trace.step === 'material_parse') group = 0;
      else if (trace.step === 'filing_rules') group = 1;
      else if (trace.step === 'ocr') group = filingRuleIndex >= 0 && index < filingRuleIndex ? 1 : 3;
      else if (['installation_group', 'installation_rules'].includes(trace.step)) group = 2;
      else if (trace.step === 'vision') group = 3;
      else if (['rule_engine', 'completeness'].includes(trace.step)) group = 4;
      else if (['geometry_group', 'equipment_inventory', 'component_lookup', 'weather_lookup', 'catastrophe_assessment', 'front_load_screening'].includes(trace.step)) group = 5;
      else if (trace.step === 'decision') group = 6;
      if (group !== undefined) groups[group].push(trace);
    });
    return groups;
  }

  function stageGroup(stage) {
    if (stage === 'filing') return 1;
    if (stage === 'installation_type' || stage === 'installation_scope') return 2;
    if (stage === 'single_image') return 3;
    if (stage === 'completeness' || stage === 'location') return 4;
    return -1;
  }

  function liveIndex(job) {
    if (!job || job.status === 'queued' || job.status === 'failed') return null;
    const progress = Number(job.progress_percent) || 0;
    if (progress >= 95) return 6;
    if (progress >= 82) return 5;
    if (progress >= 76) return 4;
    if (progress >= 35) return 3;
    if (progress >= 20) return 2;
    if (progress >= 8) return 1;
    return 0;
  }

  function derive(result, job, visited = []) {
    if (!result) {
      const active = liveIndex(job);
      return modules.map((module, index) => ({
        ...module,
        status: job?.status === 'failed' && index === active ? 'failed'
          : job?.status === 'running' && index === active ? 'active'
            : index !== active && visited.includes(index) ? 'done' : 'waiting',
        traces: [], reviews: [],
      }));
    }
    const groups = traceGroups(result);
    const reviews = modules.map(() => []);
    for (const review of result.stage_reviews || []) {
      const group = stageGroup(review.stage);
      if (group >= 0) reviews[group].push(review);
    }
    const stoppingIndex = reviews.findIndex(items => items.some(item => item.terminates_run));
    return modules.map((module, index) => {
      const traces = groups[index], stageReviews = reviews[index];
      let status = 'waiting';
      if (stageReviews.some(item => item.terminates_run && item.action === 'recommend_reject')) status = 'stopped';
      else if (stageReviews.some(item => item.terminates_run)) status = 'attention';
      else if (traces.length && traces.every(trace => trace.status === 'skipped')) status = 'skipped';
      else if (traces.some(trace => trace.status === 'failed')) status = traces.some(trace => trace.status === 'success') ? 'partial' : 'failed';
      else if (traces.some(trace => trace.status === 'partial')) status = 'partial';
      else if (stageReviews.some(item => ['request_more', 'manual_review'].includes(item.action))) status = 'attention';
      else if (index === 4 && result.decision?.decision === 'request_more') status = 'attention';
      else if (index === 5 && result.pre_catastrophe_outcome === 'request_more') status = result.catastrophe_assessment ? 'attention' : 'skipped';
      else if (traces.some(trace => trace.status === 'success')) status = 'done';
      else if (stoppingIndex >= 0 && index > stoppingIndex && index < 6) status = 'skipped';
      else if (index === 5 && result.pre_catastrophe_outcome && result.pre_catastrophe_outcome !== 'continue') status = 'skipped';
      if (status === 'waiting') status = 'unrecorded';
      return { ...module, status, traces, reviews: stageReviews };
    });
  }

  function addSection(parent, title) { parent.append(node('h4', 'ap-section-label', title)); }
  function addRow(parent, label, content, tone = '') {
    const row = node('div', 'ap-data-row');
    row.append(node('span', '', label), node('strong', tone ? `ap-${tone}` : '', value(content)));
    parent.append(row);
  }
  function addParagraphs(parent, values, empty) {
    if (!values?.length && empty) parent.append(node('p', 'ap-muted', empty));
    for (const item of values || []) parent.append(node('p', 'ap-copy', item));
  }
  function addAction(parent, message, good = false) { parent.append(node('div', `ap-action${good ? ' good' : ''}`, message)); }
  function addDetails(parent, label, content) {
    const details = node('details', 'ap-details');
    details.append(node('summary', '', label), content);
    parent.append(details);
    return details;
  }
  function materialName(result, materialId) {
    return result.materials?.find(item => item.material_id === materialId)?.file_name || materialId || '未关联材料';
  }
  function filingMaterialIds(result, module) {
    const ids = new Set((result.materials || []).filter(item => filingCategories.has(item.category)).map(item => item.material_id));
    for (const trace of module.traces.filter(item => item.step === 'ocr')) {
      for (const task of trace.task_executions || []) ids.add(task.material_id);
    }
    return ids;
  }
  function filingFields(result, module) {
    const ids = filingMaterialIds(result, module);
    return (result.ocr_fields || []).filter(field => ids.has(field.material_id));
  }
  function rawOutput(result, module, index) {
    const review = module.reviews;
    if (index === 0) return { materials: result.materials };
    if (index === 1) return { ocr_fields: filingFields(result, module), stage_reviews: review };
    if (index === 2) return { installation_analysis: result.installation_analysis, stage_reviews: review };
    if (index === 3) {
      const filingIds = filingMaterialIds(result, { traces: traceGroups(result)[1] });
      return { findings: result.findings, ocr_fields: (result.ocr_fields || []).filter(field => !filingIds.has(field.material_id)), material_reviews: result.material_reviews, stage_reviews: review };
    }
    if (index === 4) return { package_assessment: result.package_assessment, applicable_checks: result.applicable_checks, check_results: result.check_results, stage_reviews: review };
    if (index === 5) return { geometry_analysis: result.geometry_analysis, equipment_inventory: result.equipment_inventory, component_profile: result.component_profile, location_assessment: result.location_assessment, weather_profile: result.weather_profile, catastrophe_assessment: result.catastrophe_assessment, pv_load_screening_assessment:result.pv_load_screening_assessment, component_module_result:result.component_module_result };
    return { decision: result.decision };
  }
  function fieldEvidence(result, field) {
    const assessment = field.filing_assessment;
    return {
      materialId: field.material_id, source: materialName(result, field.material_id),
      label: fieldNames[field.field_name] || field.field_name,
      quote: field.raw_value || field.evidence_text || field.normalized_value,
      fragment: assessment?.evidence_quote || null,
      page: field.page_number,
      bbox: assessment?.evidence_bbox || field.bbox || null,
      locationType: assessment?.evidence_bbox ? '违规片段定位' : field.bbox ? '字段定位' : null,
    };
  }
  function findingEvidence(result, finding) {
    return {
      materialId: finding.material_id, source: materialName(result, finding.material_id),
      label: finding.label, quote: finding.evidence_text, bbox: finding.bbox || null,
      locationType: finding.bbox ? '画面线索定位' : null,
    };
  }
  function materialEvidence(result, materialId, label = '来源材料') {
    return { materialId, source: materialName(result, materialId), label, quote: null };
  }
  function linkedEvidence(result, review) {
    const evidence = [];
    for (const id of review.ocr_field_ids || []) {
      const field = (result.ocr_fields || []).find(item => item.field_id === id);
      if (field) evidence.push(fieldEvidence(result, field));
    }
    for (const id of review.finding_ids || []) {
      const finding = (result.findings || []).find(item => item.finding_id === id);
      if (finding) evidence.push(findingEvidence(result, finding));
    }
    const linkedIds = new Set(evidence.map(item => item.materialId));
    for (const id of review.material_ids || []) if (!linkedIds.has(id)) evidence.push(materialEvidence(result, id));
    if (!evidence.length) for (const summary of review.evidence_texts || []) {
      evidence.push({ source: '规则依据摘要', label: '已保存的证据文字', quote: summary });
    }
    return evidence;
  }
  function risk(tone, title, detail, evidence = [], meta = {}) {
    return { tone, title, detail, evidence, action: meta.action || null, ruleIds: meta.ruleIds || [], origin: meta.origin || null };
  }
  function reviewRisk(result, review, title) {
    const tone = review.action === 'recommend_reject' ? 'reject' : review.action === 'request_more' ? 'missing' : 'pending';
    const detail = [...(review.reasons || []), ...(review.pending_questions || []).map(item => `待核：${item}`), ...(review.customer_requests || []).map(item => `需补充：${item}`)].join('；');
    return risk(tone, title, detail, linkedEvidence(result, review), {
      action: actionNames[review.action] || review.action, ruleIds: review.rule_ids || [], origin: '规则引擎',
    });
  }
  function deriveInsights(result, module, index) {
    const risks = [], contextEvidence = [];
    if (!result || ['skipped', 'waiting', 'unrecorded'].includes(module.status)) return { risks, contextEvidence };
    if (index === 0) {
      for (const material of result.materials || []) {
        contextEvidence.push(materialEvidence(result, material.material_id, `画质：${material.quality_status || '未记录'} · 解析：${material.parse_status || '未记录'}`));
        const issues = [...(material.quality_issues || [])];
        if (material.parse_status === 'failed') issues.push('材料解析失败');
        if (material.quality_status === 'unusable') issues.push('材料不可读');
        if (material.duplicate_of_material_id) issues.push(`与 ${materialName(result, material.duplicate_of_material_id)} 重复`);
        if (issues.length) risks.push(risk('missing', `${material.file_name}：材料预检需处理`, [...new Set(issues)].join('；'), [materialEvidence(result, material.material_id, '原始材料')], { origin: '材料预检' }));
      }
    } else if (index === 1) {
      const fields = filingFields(result, module);
      const actionable = module.reviews.find(item => item.action === 'recommend_reject') || module.reviews.find(item => item.action !== 'continue');
      for (const field of fields) {
        if (['project_name', 'construction_content'].includes(field.field_name)) contextEvidence.push(fieldEvidence(result, field));
        const assessment = field.filing_assessment;
        if (!assessment || !['confirmed', 'uncertain'].includes(assessment.status)) continue;
        const confirmedReject = assessment.status === 'confirmed' && actionable?.action === 'recommend_reject' && (actionable.ocr_field_ids || []).includes(field.field_id);
        risks.push(risk(confirmedReject ? 'reject' : 'pending', `${fieldNames[field.field_name] || field.field_name}：${confirmedReject ? '禁投依据已确认' : '禁投线索待核'}`, assessment.reason, [fieldEvidence(result, field)], {
          action: actionable ? actionNames[actionable.action] || actionable.action : '待规则核验',
          ruleIds: actionable?.rule_ids || [], origin: '模型字段判断 → 备案规则',
        }));
      }
      if (actionable?.action === 'recommend_reject' && !risks.some(item => item.tone === 'reject')) risks.push(reviewRisk(result, actionable, '备案规则触发建议拒保'));
      if (!risks.length) for (const review of module.reviews.filter(item => item.action !== 'continue')) risks.push(reviewRisk(result, review, '备案材料需要处理'));
    } else if (index === 2) {
      for (const area of result.installation_analysis?.areas || []) {
        for (const region of area.evidence_regions || []) contextEvidence.push({ materialId: region.material_id, source: materialName(result, region.material_id), label: '安装类型画面依据', quote: region.description, bbox: region.bbox, locationType: region.bbox ? '场景定位' : null, locationExpected: true });
        if (!area.evidence_regions?.length) for (const id of area.material_ids || []) contextEvidence.push({ ...materialEvidence(result, id, '组图安装依据'), quote: area.evidence_text });
      }
      for (const review of module.reviews.filter(item => item.action !== 'continue')) {
        const item = reviewRisk(result, review, '安装类型判定需要处理');
        const cited = new Set(review.material_ids || []);
        const regions = contextEvidence.filter(evidence => !cited.size || cited.has(evidence.materialId));
        if (regions.length) item.evidence = regions;
        risks.push(item);
      }
      if (!risks.length) for (const area of result.installation_analysis?.areas || []) {
        if (['candidate', 'unknown', 'conflict'].includes(area.status)) risks.push(risk('pending', '安装类型尚未确认', area.pending_question || area.evidence_text, contextEvidence, { origin: '组图模型' }));
      }
    } else if (index === 3) {
      const reviewedFindingIds = new Set();
      for (const review of result.material_reviews || []) {
        if (['pass', 'not_applicable'].includes(review.action)) continue;
        if (review.action === 'warning' && review.triggered_rule_ids?.length && review.triggered_rule_ids.every(id => id === 'DOC-COMPARISON-FIELD-UNAVAILABLE-001')) {
          contextEvidence.push(materialEvidence(result, review.material_id, '交叉核对范围提示'));
          continue;
        }
        (review.finding_ids || []).forEach(id => reviewedFindingIds.add(id));
        // Installation findings belong to the group-image module. A per-file
        // review may cite every finding on that photo, including those from
        // another module, so do not repeat them as single-image risk evidence.
        const singleFindingIds = (review.finding_ids || []).filter(id =>
          (result.findings || []).find(finding => finding.finding_id === id)?.category !== 'installation_type');
        const pseudoReview = { ...review, finding_ids: singleFindingIds, material_ids: [review.material_id], rule_ids: review.triggered_rule_ids, evidence_texts: [] };
        const item = reviewRisk(result, pseudoReview, `${materialName(result, review.material_id)}：${materialActionNames[review.action] || review.action}`);
        item.tone = review.action === 'recommend_reject' ? 'reject' : review.action === 'request_more' ? 'missing' : 'warning';
        item.action = materialActionNames[review.action] || review.action;
        risks.push(item);
      }
      for (const finding of result.findings || []) {
        if (['installation_type', 'image_quality', 'monitoring_effective_coverage'].includes(finding.category)) continue;
        if (!['detected', 'uncertain'].includes(finding.detection_status)) continue;
        const evidence = findingEvidence(result, finding);
        contextEvidence.push(evidence);
        if (!reviewedFindingIds.has(finding.finding_id) && finding.severity !== 'info') risks.push(risk(finding.detection_status === 'uncertain' ? 'pending' : 'warning', `${finding.label}：${finding.detection_status === 'uncertain' ? '待核线索' : '模型发现'}`, finding.evidence_text, [evidence], { origin: '单图模型；未单独命中材料规则' }));
      }
      for (const review of module.reviews.filter(item => item.action !== 'continue')) {
        if (!risks.length) risks.push(reviewRisk(result, review, '单图阶段需要处理'));
      }
    } else if (index === 4) {
      for (const check of result.check_results || []) {
        const definition = (result.applicable_checks || []).find(item => item.area_id === check.area_id && item.key === check.key);
        const label = definition?.label || check.key;
        const evidence = linkedEvidence(result, check);
        if (check.status === 'valid') contextEvidence.push(...evidence);
        if (definition?.required_now === false && ['missing', 'not_provided'].includes(check.status)) continue;
        if (['missing', 'unusable', 'pending'].includes(check.status)) {
          if (!evidence.length) evidence.push({ source: '材料完整性检查', label: '缺失或待核依据', quote: check.reason });
          const optionalUnusable = definition?.required_now === false && check.status === 'unusable';
          risks.push(risk(optionalUnusable ? 'warning' : check.status === 'pending' ? 'pending' : 'missing', `${label}：${{ missing: '缺少材料', unusable: optionalUnusable ? '可选材料不可用' : '材料不可用', pending: '待核实' }[check.status]}`, [check.reason, check.customer_request].filter(Boolean).join('；'), evidence, { ruleIds: check.rule_ids || [], origin: '适用检查项' }));
        }
      }
      if (result.location_assessment?.address_photo_conflict) {
        const legacyReject = (result.stage_reviews || []).some(stage => stage.stage === 'location' && stage.action === 'recommend_reject');
        risks.unshift(risk(legacyReject ? 'reject' : 'pending', legacyReject ? '项目地址与现场坐标冲突，建议拒保' : '项目地址与现场坐标冲突，需人工复核', (result.location_assessment.notes || []).join('；'), (result.location_assessment.evidence || []).filter(item => item.material_id).map(item => ({ materialId: item.material_id, source: item.file_name || item.source || '位置核验', label: item.address ? '备案安装地址' : '现场水印经纬度', quote: item.address || `${item.longitude}, ${item.latitude}` })), { ruleIds: ['LOC-CONFLICT-001'], origin: '项目位置核验' }));
      }
      if (!risks.length) for (const review of module.reviews.filter(item => item.action !== 'continue')) risks.push(reviewRisk(result, review, '材料完整性需要处理'));
    } else if (index === 5) {
      const unified=result.pv_load_screening_assessment;
      if(unified)for(const h of ['wind','snow']){const row=unified[h];if(!row.load_pa||['high','critical'].includes(row.risk_level))risks.push(risk(row.load_pa?'warning':'missing',(h==='wind'?'风':'雪')+'：'+row.risk_label,row.reason||unified.label,[{source:'风雪风险量化',label:'计算来源',quote:unified.component_capacities[h].source}],{origin:'城市基本风雪压初筛'}));}
      const assessment = result.catastrophe_assessment;
      for (const item of assessment?.comparisons || []) {
        if(item.hazard === "hail") continue;
        const hazard = { wind: '风灾', hail: '雹灾', snow: '雪灾' }[item.hazard] || item.hazard;
        if (item.status !== 'insufficient' && !['high', 'critical'].includes(item.risk)) continue;
        const evidence = [{ source: '抗灾测算', label: `${hazard}比较依据`, quote: item.reason || item.formula || '未记录说明' }];
        if (result.component_profile?.source_name) evidence.push({ source: result.component_profile.source_name, label: '组件参数来源', quote: result.component_profile.source_url || result.component_profile.component_model });
        if (result.weather_profile?.source_name) evidence.push({ source: result.weather_profile.source_name, label: '历史气象来源', quote: result.weather_profile.source_url || '已保存气象数据' });
        risks.push(risk(item.status === 'insufficient' ? 'missing' : 'warning', `${hazard}：${item.status === 'insufficient' ? '数据不足' : '测算风险较高'}`, item.reason || item.formula, evidence, { ruleIds: item.rule_ids || [], origin: '抗灾量化筛查' }));
      }
      for (const review of assessment?.installation_parameter_reviews || []) {
        if (review.hazard === 'hail' || review.status !== 'pending_confirmation') continue;
        risks.push(risk('warning', `${{ wind: '风灾', hail: '雹灾', snow: '雪灾' }[review.hazard] || review.hazard}参数资料缺口`, review.explanation, [{ source: '参数适用性检查', label: '尚缺资料', quote: (review.required_evidence || []).join('；') || review.explanation }], { ruleIds: review.triggered_rule_id ? [review.triggered_rule_id] : [], origin: '抗灾量化筛查' }));
      }
      if (result.location_assessment?.status === 'conflict') {
        const legacyReject = (result.stage_reviews || []).some(stage => stage.stage === 'location' && stage.action === 'recommend_reject');
        risks.push(risk(legacyReject ? 'reject' : 'pending', legacyReject ? '项目地址与现场坐标冲突，建议拒保' : '电站位置证据存在冲突，需人工复核', (result.location_assessment.notes || []).join('；'), (result.location_assessment.evidence || []).map(item => ({ materialId: item.material_id || null, source: item.file_name || item.source || '位置核验', label: '位置来源', quote: item.address || item.geocode_reason || null })), { origin: '位置核验' }));
      }
      for (const area of result.geometry_analysis?.areas || []) for (const metric of [area.tilt_degrees, area.height_above_ground_m, area.ground_roughness]) if (metric?.status === 'estimated') for (const id of metric.material_ids || []) contextEvidence.push({ materialId: id, source: materialName(result, id), label: '组图抗灾参数依据', quote: metric.evidence_text });
      const catastropheRisks = risks.filter(item => item.origin === '抗灾量化筛查');
      if (catastropheRisks.length > 1) {
        const first = risks.findIndex(item => item.origin === '抗灾量化筛查');
        const grouped = risk(catastropheRisks.some(item => item.tone === 'warning') ? 'warning' : 'missing', '抗灾能力量化筛查', catastropheRisks.map(item => `${item.title}：${item.detail || '需核对'}`).join('；'), catastropheRisks.flatMap(item => item.evidence), { ruleIds: [...new Set(catastropheRisks.flatMap(item => item.ruleIds || []))], origin: '抗灾量化筛查' });
        risks.splice(0, risks.length, ...risks.filter(item => item.origin !== '抗灾量化筛查'));
        risks.splice(first, 0, grouped);
      }
    } else if (index === 6) {
      const decision = result.decision;
      for (const warning of decision?.warnings || []) {
        if (warning.startsWith('交叉核对范围提示：')) continue;
        const missingStartDate = !result.project?.proposed_start_date && warning.includes('起保日期');
        const datedPhotos = missingStartDate ? (result.ocr_fields || [])
          .filter(field => ['watermark_date', 'shot_date'].includes(field.field_name)
            && field.value_status === 'extracted' && validBox(field.bbox))
          .map(field => fieldEvidence(result, field)) : [];
        risks.push(risk('warning', '整单汇总风险提示', warning, missingStartDate
          ? datedPhotos.length ? datedPhotos : [{ source: '投保项目资料', label: '申报字段缺失', quote: '拟起保日期未填写；照片拍摄日期未取得可靠定位框' }]
          : [], { origin: '整单建议；不自动视为本节点新增事实' }));
      }
      for (const requirement of decision?.missing_requirements || []) risks.push(risk('missing', '整单补充要求', requirement, [], { origin: '整单建议；请核对前序节点' }));
      for (const reason of decision?.reasons || []) {
        if (reason.startsWith('交叉核对范围提示：')) continue;
        const reviews = (result.stage_reviews || []).filter(item => item.action !== 'continue' && (item.reasons || []).some(text => text === reason));
        const evidence = reviews.flatMap(item => linkedEvidence(result, item));
        risks.push(risk('inherited', '前序结论引用', reason, evidence, { ruleIds: decision.decisive_rule_ids || [], origin: '整单建议汇总前序依据' }));
      }
    }
    return { risks, contextEvidence };
  }
  function appendQuote(parent, quote, fragment) {
    const source = value(quote);
    if (!fragment || !source.includes(fragment)) { parent.append(document.createTextNode(source)); return; }
    const start = source.indexOf(fragment);
    parent.append(document.createTextNode(source.slice(0, start)), node('mark', '', fragment), document.createTextNode(source.slice(start + fragment.length)));
  }
  function validBox(box) {
    if (!box) return null;
    const numbers = [box.x_min, box.y_min, box.x_max, box.y_max].map(Number);
    if (numbers.some(number => !Number.isFinite(number) || number < 0 || number > 1)) return null;
    if (numbers[2] <= numbers[0] || numbers[3] <= numbers[1]) return null;
    return numbers;
  }
  function groupEvidence(evidence) {
    const groups = new Map();
    evidence.forEach((item, index) => {
      // Only a material id proves that two facts refer to the same uploaded original.
      const key = item.materialId ? `material:${item.materialId}` : `unlinked:${index}`;
      if (!groups.has(key)) groups.set(key, { materialId: item.materialId || null, source: item.source, items: [] });
      const group = groups.get(key);
      const fingerprint = JSON.stringify([item.label, item.quote, item.fragment, item.page, item.bbox]);
      if (!group.items.some(existing => existing.fingerprint === fingerprint)) group.items.push({ ...item, fingerprint });
    });
    return [...groups.values()];
  }
  function renderEvidence(parent, evidence, resolveSource) {
    if (!evidence.length) {
      parent.append(node('p', 'ap-insight-empty', '这项提示没有单独保存可定位的原件证据。请核对下方结构化结果和前序模块。'));
      return;
    }
    for (const group of groupEvidence(evidence)) {
      const card = node('article', 'ap-evidence-card');
      const heading = node('div', 'ap-evidence-heading');
      heading.append(node('strong', '', group.source || '未关联材料'), node('span', '', `${group.items.length} 条证据事实`));
      card.append(heading);
      const source = group.materialId ? resolveSource(group.materialId) : null;
      const picture = source?.url && !source.pdf ? node('div', 'ap-evidence-picture') : null;
      const overlays = group.items.map(item => {
        const bbox = validBox(item.bbox);
        if (!picture || !bbox) return null;
        const overlay = node('span', 'ap-evidence-box');
        overlay.style.left = `${bbox[0] * 100}%`;
        overlay.style.top = `${bbox[1] * 100}%`;
        overlay.style.width = `${(bbox[2] - bbox[0]) * 100}%`;
        overlay.style.height = `${(bbox[3] - bbox[1]) * 100}%`;
        return overlay;
      });
      const initial = overlays.findIndex(Boolean);
      const facts = node('div', 'ap-evidence-facts');
      const controls = [];
      const select = selected => {
        controls.forEach((control, position) => control.setAttribute('aria-pressed', String(position === selected)));
        overlays.forEach((overlay, position) => { if (overlay) overlay.hidden = position !== selected; });
      };
      group.items.forEach((item, position) => {
        const bbox = validBox(item.bbox);
        const selectable = Boolean(overlays[position]);
        const fact = node(selectable ? 'button' : 'div', `ap-evidence-fact${selectable ? ' selectable' : ''}`);
        if (selectable) {
          fact.type = 'button';
          fact.addEventListener('click', () => select(position));
          controls[position] = fact;
        }
        fact.append(node('strong', '', [item.label || '证据事实', item.page ? `第 ${item.page} 页` : null].filter(Boolean).join(' · ')));
        if (item.quote) {
          const quote = node('p', 'ap-evidence-quote');
          appendQuote(quote, item.quote, item.fragment);
          fact.append(quote);
        }
        if (bbox) fact.append(node('p', 'ap-evidence-location', `${item.locationType || '原件位置'}：(${bbox[0].toFixed(2)}, ${bbox[1].toFixed(2)})–(${bbox[2].toFixed(2)}, ${bbox[3].toFixed(2)})${selectable ? ' · 点击查看画框' : ''}`));
        else if (item.locationExpected) fact.append(node('p', 'ap-evidence-location ap-evidence-unlocated', '模型未返回可靠的画面坐标，本张原件暂无法画框。'));
        facts.append(fact);
      });
      card.append(facts);
      if (source?.url) {
        const link = node('a', 'ap-source-link', '打开原件');
        link.href = source.url; link.target = '_blank'; link.rel = 'noopener';
        card.append(link);
        if (picture) {
          const viewport = node('div', 'ap-evidence-media');
          const image = node('img');
          image.src = source.url; image.alt = `证据原件：${group.source || '材料'}`;
          picture.append(image);
          overlays.forEach(overlay => { if (overlay) picture.append(overlay); });
          viewport.append(picture); card.append(viewport);
          if (initial >= 0) select(initial);
        }
      }
      parent.append(card);
    }
  }
  function renderInsightPanel(parent, result, module, index, resolveSource) {
    const { risks, contextEvidence } = deriveInsights(result, module, index);
    const summary = node('div', 'ap-insight-summary');
    const newRisks = risks.filter(item => item.tone !== 'inherited');
    const evidenceCount = groupEvidence(risks.flatMap(item => item.evidence).concat(contextEvidence)).length;
    summary.append(node('span', '', `本节点风险与提示 ${module.status === 'skipped' ? '未运行' : newRisks.length + ' 项'}`), node('span', '', `证据 ${module.status === 'skipped' ? '无' : evidenceCount + ' 组'}`));
    if (risks.length > newRisks.length) summary.append(node('span', '', `前序结论引用 ${risks.length - newRisks.length} 项`));
    parent.append(summary);
    const grid = node('div', 'ap-insight-grid');
    const riskPanel = node('div', 'ap-risk-panel');
    const evidencePanel = node('div', 'ap-evidence-panel');
    grid.append(riskPanel, evidencePanel); parent.append(grid);
    addSection(riskPanel, '本节点风险与提示');
    addSection(evidencePanel, risks.length ? '对应证据' : '本节点输入与证据');
    const evidenceBody = node('div', 'ap-evidence-body');
    evidencePanel.append(evidenceBody);
    if (!risks.length) {
      riskPanel.append(node('p', 'ap-insight-empty', module.status === 'skipped' ? '本模块未启动，没有新产生的风险提示。' : module.status === 'unrecorded' ? '历史案件未保存本模块的执行记录，无法判断当时的风险。' : '本节点没有新产生的风险提示。'));
      renderEvidence(evidenceBody, contextEvidence, resolveSource);
      return;
    }
    const controls = [];
    const showEvidence = selected => {
      controls.forEach((button, position) => button.setAttribute('aria-pressed', String(position === selected)));
      evidenceBody.replaceChildren();
      renderEvidence(evidenceBody, risks[selected].evidence, resolveSource);
    };
    risks.forEach((item, position) => {
      const button = node('button', `ap-risk-card ${item.tone}`);
      button.type = 'button';
      button.setAttribute('aria-pressed', String(position === 0));
      button.append(node('span', 'ap-risk-type', item.tone === 'reject' ? '拒保依据' : item.tone === 'missing' ? '需补充' : item.tone === 'pending' ? '待核实' : item.tone === 'inherited' ? '前序引用' : '风险提示'), node('strong', '', item.title));
      if (item.detail) button.append(node('span', 'ap-risk-detail', item.detail));
      const footer = [item.origin, item.action, item.ruleIds.length ? `规则 ${item.ruleIds.join('、')}` : null].filter(Boolean).join(' · ');
      if (footer) button.append(node('small', '', footer));
      button.addEventListener('click', () => showEvidence(position));
      controls.push(button); riskPanel.append(button);
    });
    showEvidence(0);
  }
  function addRaw(parent, result, module, index) {
    const pre = node('pre', 'ap-json', JSON.stringify(rawOutput(result, module, index), null, 2));
    addDetails(parent, '查看本模块结构化原始结果', pre);
  }

  function renderMaterialOutput(parent, result) {
    addSection(parent, '材料预检结果');
    const materials = result.materials || [];
    addRow(parent, '提交材料', `${materials.length} 份`);
    addRow(parent, '可分析', `${materials.filter(item => item.parse_status === 'success' && item.quality_status !== 'unusable' && !item.duplicate_of_material_id).length} 份`);
    addRow(parent, '重复材料', `${materials.filter(item => item.duplicate_of_material_id).length} 份`);
    for (const item of materials) {
      const line = node('div', 'ap-output-item');
      line.append(node('strong', '', item.file_name), node('span', '', `${item.category} · 画质 ${item.quality_status} · 解析 ${item.parse_status}`));
      if (item.quality_issues?.length) line.append(node('p', '', item.quality_issues.join('；')));
      parent.append(line);
    }
  }

  function renderFilingOutput(parent, result, module) {
    const fields = filingFields(result, module);
    addSection(parent, '备案关键字段与模型判断');
    if (!fields.length) parent.append(node('p', 'ap-muted', '本轮没有可展示的备案字段；查看内部步骤及材料状态。'));
    for (const field of fields) {
      const body = node('div', 'ap-field-detail');
      addRow(body, '原文', field.raw_value ?? field.normalized_value);
      addRow(body, '提取状态', field.value_status);
      if (field.filing_assessment) {
        const filing = field.filing_assessment;
        const generation = {distributed:'分布式', centralized:'集中式'}[filing.generation_mode];
        const site = {building_roof:'建筑屋顶', carport_roof:'车棚顶', other:'非屋顶／非车棚'}[filing.installation_site];
        if (generation) addRow(body, '备案所述项目类型', generation);
        if (site) addRow(body, '备案所述安装位置', site);
        addRow(body, '违规判断', field.filing_assessment.status === 'confirmed' ? '确认命中' : field.filing_assessment.status === 'uncertain' ? '待核实' : '未发现');
        addRow(body, '违规片段', field.filing_assessment.evidence_quote);
        addRow(body, '判断理由', field.filing_assessment.reason);
      }
      addRow(body, '证据来源', `${materialName(result, field.material_id)}${field.page_number ? ` · 第 ${field.page_number} 页` : ''}`);
      const box = field.filing_assessment?.evidence_bbox;
      if (box) addRow(body, '原件位置', `(${box.x_min}, ${box.y_min})—(${box.x_max}, ${box.y_max})`);
      const details = addDetails(parent, `${fieldNames[field.field_name] || field.field_name} · ${value(field.raw_value ?? field.normalized_value)}`, body);
      if (['project_name', 'construction_content'].includes(field.field_name)) details.open = true;
    }
    renderStageReviews(parent, module.reviews);
  }

  function renderStageReviews(parent, reviews) {
    if (!reviews.length) return;
    addSection(parent, '规则动作');
    for (const review of reviews) {
      addAction(parent, `${actionNames[review.action] || review.action}${review.rule_ids?.length ? ` · ${review.rule_ids.join('、')}` : ''}`, review.action === 'continue');
      addParagraphs(parent, review.reasons);
      if (review.pending_questions?.length) addParagraphs(parent, review.pending_questions.map(item => `待核：${item}`));
      if (review.customer_requests?.length) addParagraphs(parent, review.customer_requests.map(item => `需补充：${item}`));
    }
  }

  function renderInstallationOutput(parent, result, module) {
    addSection(parent, '整单组图识别输出');
    const analysis = result.installation_analysis;
    if (!analysis?.areas?.length) parent.append(node('p', 'ap-muted', '未形成安装类型结果。'));
    for (const area of analysis?.areas || []) {
      addRow(parent, '安装类型', sceneNames[area.scene_type] || area.scene_type);
      addRow(parent, '判断状态', area.status);
      addRow(parent, '承载结构', area.supporting_structure);
      addRow(parent, '用途', area.usage);
      addRow(parent, '图片证据', area.material_ids?.map(id => materialName(result, id)).join('、'));
      addParagraphs(parent, [area.evidence_text]);
      if (area.pending_question) addParagraphs(parent, [`待核：${area.pending_question}`]);
      for (const region of area.evidence_regions || []) addParagraphs(parent, [`${materialName(result, region.material_id)}：${region.description}`]);
    }
    renderStageReviews(parent, module.reviews);
  }

  function renderSingleOutput(parent, result, module) {
    const filingIds = filingMaterialIds(result, { traces: traceGroups(result)[1] });
    const fields = (result.ocr_fields || []).filter(field => !filingIds.has(field.material_id));
    const findings = result.findings || [];
    addSection(parent, '逐图结构化输出');
    addRow(parent, '风险识别记录', `${findings.length} 项`);
    addRow(parent, '非备案文字字段', `${fields.length} 项`);
    const ids = [...new Set([...findings.map(item => item.material_id), ...fields.map(item => item.material_id)])];
    if (!ids.length) parent.append(node('p', 'ap-muted', '没有形成逐图识别结果。'));
    for (const id of ids) {
      const materialFindings = findings.filter(item => item.material_id === id);
      const materialFields = fields.filter(item => item.material_id === id);
      const body = node('div', 'ap-field-detail');
      for (const finding of materialFindings) {
        const item = node('div', 'ap-output-item');
        item.append(node('strong', '', `${finding.label} · ${finding.detection_status}`), node('p', '', finding.evidence_text));
        body.append(item);
      }
      for (const field of materialFields) addRow(body, field.field_name, field.raw_value ?? field.normalized_value ?? field.value_status);
      const review = (result.material_reviews || []).find(item => item.material_id === id);
      if (review) addRow(body, '单份规则动作', `${actionNames[review.action] || review.action} · ${(review.triggered_rule_ids || []).join('、') || '未命中规则'}`);
      addDetails(parent, `${materialName(result, id)} · ${materialFindings.length} 项图片结果 / ${materialFields.length} 项文字结果`, body);
    }
    renderStageReviews(parent, module.reviews);
  }

  function renderCompletenessOutput(parent, result, module) {
    addSection(parent, '材料完整性与位置核验');
    const assessment = result.package_assessment;
    if (assessment) {
      addRow(parent, '采用配置', assessment.requirement_profile);
      addRow(parent, '现场照片', `${assessment.image_count} 张`);
      addRow(parent, '全景照片', `${assessment.panorama_count} 张`);
      addRow(parent, '备案材料', assessment.has_filing_certificate ? '已提供' : '未提供');
      addRow(parent, '数量判定', '仅作覆盖统计，不按固定张数触发补材');
    } else parent.append(node('p', 'ap-muted', '本轮未形成材料包判定。'));
    addRow(parent, '前置结论', actionNames[result.pre_catastrophe_outcome] || result.pre_catastrophe_outcome);
    if (result.location_assessment) {
      const legacyReject = (result.stage_reviews || []).some(stage => stage.stage === 'location' && stage.action === 'recommend_reject');
      addRow(parent, '位置核验', result.location_assessment.status === 'conflict' ? legacyReject ? '位置冲突，建议拒保' : '位置冲突，需人工复核' : result.location_assessment.status === 'distance_screened' ? '500 米原始距离初筛通过' : result.location_assessment.status);
      addParagraphs(parent, result.location_assessment.notes);
    }
    const checks = result.check_results || [];
    if (checks.length) {
      addSection(parent, `适用检查项 · ${checks.length}`);
      for (const check of checks) {
        const definition = (result.applicable_checks || []).find(item => item.area_id === check.area_id && item.key === check.key);
        const label = definition?.label || check.key;
        const statusLabel = definition?.required_now === false && ['missing', 'not_provided'].includes(check.status) ? '可选未提供' : check.status === 'unassessed' ? '未返回识别结果（仅提示）' : check.status;
        const body = node('div', 'ap-field-detail');
        addRow(body, '检查状态', statusLabel);
        addRow(body, '材料要求', definition ? (definition.required_now ? '本轮必需' : '可选参考') : '未记录');
        addRow(body, '证据材料', check.material_ids?.map(id => materialName(result, id)).join('、'));
        addRow(body, '判断依据', check.reason);
        if (check.customer_request) addRow(body, '补材要求', check.customer_request);
        addDetails(parent, `${label} · ${statusLabel}`, body);
      }
    }
    renderStageReviews(parent, module.reviews);
  }

  function renderCatastropheOutput(parent, result) {
    addSection(parent, '抗灾参数与能力筛查');
    const geometry = result.geometry_analysis;
    for (const area of geometry?.areas || []) {
      for (const [label, metric] of [['光伏板倾角', area.tilt_degrees], ['离地高度', area.height_above_ground_m], ['地面粗糙度', area.ground_roughness]]) {
        const numeric = metric.value_min == null || metric.value_max == null ? null : `${metric.value_min}—${metric.value_max}${label === '光伏板倾角' ? '°' : label === '离地高度' ? ' m' : ''}`;
        addRow(parent, label, metric.roughness_candidate || numeric || '未知');
        if (metric.evidence_text) addParagraphs(parent, [`${label}依据：${metric.evidence_text}`]);
      }
    }
    if (!geometry) parent.append(node('p', 'ap-muted', '本轮没有组图抗灾参数输出。'));
    addRow(parent, '设备清单', `${(result.equipment_inventory || []).length} 项`);
    addRow(parent, '组件型号', result.component_profile?.component_model);
    addRow(parent, '组件参数来源', result.component_profile?.source_name);
    addRow(parent, '历史气象', result.weather_profile ? '已取得' : '未取得');
    addRow(parent, '位置状态', result.location_assessment?.status);
    const unified=result.pv_load_screening_assessment;
    if(unified){addRow(parent,'风雪风险量化',unified.label);for(const h of ['wind','snow']){const row=unified[h];addRow(parent,h==='wind'?'风':'雪',row.load_pa?`${row.load_pa.min.toFixed(1)}～${row.load_pa.max.toFixed(1)} Pa · ${row.risk_label}`:row.reason);}addParagraphs(parent,unified.actions||[]);return;}
    const assessment = result.catastrophe_assessment;
    if (assessment) {
      addRow(parent, '综合抗灾等级', assessment.resistance_level);
      addParagraphs(parent, [assessment.explanation]);
      addSection(parent, '风、雪逐项测算');
      for (const item of assessment.comparisons || []) {
        if(item.hazard === "hail") continue;
        const name = { wind: '风灾', hail: '雹灾', snow: '雪灾' }[item.hazard] || item.hazard;
        addRow(parent, name, `${item.status === 'calculated' ? `能力比 ${value(item.ratio)}` : '数据不足'} · ${value(item.reason)}`);
      }
    } else parent.append(node('p', 'ap-muted', '本轮没有抗灾量化结果。'));
  }

  function renderDecisionOutput(parent, result) {
    const decision = result.decision;
    addSection(parent, '整单系统建议');
    if (!decision) { parent.append(node('p', 'ap-muted', '未生成整单建议。')); return; }
    const priceSuffix = ['accept', 'conditional_accept'].includes(decision.decision)
      && decision.pricing_recommendation === 'surcharge' ? '（加费）' : '';
    addAction(parent, (decisionNames[decision.decision] || decision.decision) + priceSuffix,
      decision.decision === 'accept');
    addRow(parent, '决定性规则', decision.decisive_rule_ids?.join('、'));
    addParagraphs(parent, decision.reasons);
    if (decision.warnings?.length) { addSection(parent, '风险提示'); addParagraphs(parent, decision.warnings); }
    if (decision.missing_requirements?.length) { addSection(parent, '补充要求'); addParagraphs(parent, decision.missing_requirements); }
    if (decision.conditions?.length) { addSection(parent, '承保条件'); addParagraphs(parent, decision.conditions); }
  }

  function renderTracePanel(parent, module, result, index) {
    addSection(parent, '模块内部节点');
    if (!module.traces.length) parent.append(node('p', 'ap-muted', '没有单独保存的执行记录。'));
    for (const trace of module.traces) {
      const item = node('div', 'ap-trace');
      item.append(node('strong', '', stepNames[trace.step] || trace.step), node('span', `ap-trace-status ${trace.status}`, trace.status === 'success' ? '完成' : trace.status === 'skipped' ? '跳过' : trace.status === 'partial' ? '部分完成' : '失败'));
      if (trace.detail) item.append(node('p', '', trace.detail));
      if (trace.error_message) item.append(node('p', 'ap-error', trace.error_message));
      item.append(node('small', '', `${trace.provider || '未记录来源'}${trace.model ? ` · ${trace.model}` : ''} · ${((trace.latency_ms || 0) / 1000).toFixed(1)} 秒`));
      if (trace.task_executions?.length) for (const task of trace.task_executions) item.append(node('small', '', `${materialName(result, task.material_id)} · ${task.task === 'watermark' ? '水印' : '业务文字'} · ${task.status}${task.reason ? `：${task.reason}` : ''}`));
      parent.append(item);
    }
    if ([1, 2, 3, 5].includes(index) && module.status !== 'skipped') parent.append(node('p', 'ap-footnote', '这里展示已保存的结构化识别结果。当前案件记录未单独保存模型 API 的完整原始响应。'));
    if (index === 5 && module.status !== 'skipped') parent.append(node('p', 'ap-footnote', '程序执行完成与数据足以形成抗灾判断分别显示；缺失的风雪数据不视为通过。'));
  }

  function render(root, { result = null, job = null, visited = [], selected = 0, onSelect = () => {}, resolveSource = () => null, renderReport = null, durationMs = null, stale = false } = {}) {
    const items = derive(result, job, visited);
    const chosen = Math.max(0, Math.min(modules.length - 1, selected));
    root.replaceChildren();
    const wrapper = node('div', 'ap-root');
    root.append(wrapper);
    const heading = node('div', 'ap-heading');
    heading.append(node('div', 'ap-heading-copy'));
    heading.firstChild.append(node('h2', '', '分析过程'), node('p', '', result?.case_id ? `案件 ${result.case_id} · 按业务模块查看输出与规则依据` : '运行到哪个业务模块，就在顶部显示对应状态。'));
    wrapper.append(heading);

    const banner = node('div', 'ap-banner');
    let title, subtitle, icon = '!';
    if (job?.status === 'running' || job?.status === 'queued') {
      const active = liveIndex(job);
      title = active === null ? '任务已提交，等待开始' : `正在运行：${modules[active].title}`;
      subtitle = job.current_step || '等待服务更新进度';
      icon = '…';
    } else if (job?.status === 'failed') {
      title = '本次整单运行失败'; subtitle = job.current_step || job.error_code || '查看服务处理记录';
    } else if (result) {
      const stop = items.find(item => item.status === 'stopped');
      title = stop ? `${stop.short}触发提前拒保，已生成整单建议` : result.pre_catastrophe_outcome && result.pre_catastrophe_outcome !== 'continue' ? '前置初审已结束，已生成整单建议' : '整单运行完成，已生成系统建议';
      subtitle = stop ? `中间未运行的模块已标为“跳过”；${decisionNames[result.decision?.decision] || '请查看整单建议'}` : decisionNames[result.decision?.decision] || '查看各模块结果';
      icon = stop ? '!' : '✓';
    } else {
      title = '等待运行整条链路'; subtitle = '上传并确认材料后，点击“运行整条链路”。'; icon = '·';
    }
    banner.append(node('span', `ap-banner-icon${icon === '✓' ? ' good' : ''}`, icon));
    const bannerCopy = node('div', 'ap-banner-copy');
    bannerCopy.append(node('strong', '', title), node('span', '', subtitle));
    banner.append(bannerCopy);
    if (durationMs !== null && Number.isFinite(durationMs)) banner.append(node('span', 'ap-duration', `总耗时 ${Math.max(1, Math.ceil(durationMs / 1000))} 秒`));
    if (stale && !job) banner.append(node('span', 'ap-stale', '上一轮结果'));
    wrapper.append(banner);

    const nav = node('nav', 'ap-stepper');
    nav.setAttribute('aria-label', '七个业务模块');
    items.forEach((module, index) => {
      const button = node('button', `ap-step ${module.status}${index > 0 && items[index - 1].status === 'done' && module.status === 'done' ? ' connected' : ''}`);
      button.type = 'button';
      button.setAttribute('aria-pressed', String(index === chosen));
      button.setAttribute('aria-label', `${module.title}：${stateNames[module.status]}`);
      button.append(node('span', 'ap-step-marker', module.status === 'done' ? '✓' : module.status === 'stopped' ? '!' : module.status === 'active' ? '…' : index + 1), node('span', 'ap-step-name', module.short), node('span', 'ap-step-state', stateNames[module.status]));
      button.addEventListener('click', () => onSelect(index));
      nav.append(button);
    });
    wrapper.append(nav);

    const module = items[chosen];
    const panel = node('div', 'ap-panel');
    const panelHeading = node('div', 'ap-panel-heading');
    const labels = node('div');
    labels.append(node('span', 'ap-kicker', `业务模块 ${String(chosen + 1).padStart(2, '0')} / 07`), node('h3', '', module.title), node('p', '', module.subtitle));
    panelHeading.append(labels, node('span', `ap-badge ${module.status}`, stateNames[module.status]));
    panel.append(panelHeading);
    if (result && chosen === modules.length - 1 && typeof renderReport === 'function') {
      const reportContent = node('div', 'ap-report-content');
      panel.append(reportContent);
      wrapper.append(panel);
      renderReport(reportContent, result);
      return items;
    }
    if (result) {
      const insight = node('section', 'ap-insight-panel');
      renderInsightPanel(insight, result, module, chosen, resolveSource);
      panel.append(insight);
    }
    const fullHeading = node('div', 'ap-full-heading');
    fullHeading.append(node('strong', '', '详细结构化输出与执行记录'), node('span', '', '完整字段、规则结果与模块执行明细'));
    panel.append(fullHeading);
    const grid = node('div', 'ap-detail-grid');
    const content = node('div', 'ap-output');
    const tracePanel = node('div', 'ap-execution');
    grid.append(content, tracePanel);
    panel.append(grid);
    wrapper.append(panel);

    if (!result) {
      content.append(node('p', 'ap-empty', module.status === 'active' ? '当前模块正在运行。完成后可查看结构化输出、原文证据与规则动作。' : module.status === 'failed' ? '本次运行在此阶段失败，没有形成可用结果。' : '该模块尚未形成输出。'));
      tracePanel.append(node('p', 'ap-empty', job?.current_step || '运行完成后显示模型、耗时和内部节点状态。'));
      return items;
    }
    if (module.status === 'skipped' || module.status === 'unrecorded') {
      content.append(node('p', 'ap-empty', module.status === 'skipped' ? '本模块未启动，因此没有模型输出或核保通过结论。' : '这份历史案件没有保存该模块的执行记录，无法判断当时是否运行。'));
      if (module.status === 'skipped') {
        const reason = module.traces.find(trace => trace.status === 'skipped' && trace.detail)?.detail || '前置阶段已终止本轮分析。';
        addAction(content, `跳过原因：${reason}`);
      }
    } else {
      [renderMaterialOutput, renderFilingOutput, renderInstallationOutput, renderSingleOutput, renderCompletenessOutput, renderCatastropheOutput, renderDecisionOutput][chosen](content, result, module);
      addRaw(content, result, module, chosen);
    }
    renderTracePanel(tracePanel, module, result, chosen);
    return items;
  }

  global.AnalysisProgress = { modules, traceGroups, liveIndex, derive, deriveInsights, groupEvidence, renderEvidence, render };
  if (typeof module !== 'undefined') module.exports = global.AnalysisProgress;
})(typeof window === 'undefined' ? globalThis : window);
