const test = require('node:test');
const assert = require('node:assert/strict');
const progress = require('../../frontend/analysis-progress.js');

const trace = (step, status = 'success') => ({ step, status, task_executions: [] });

test('交叉核对字段未识别只作范围提示，不进入风险或复核列表', () => {
  const notice = '交叉核对范围提示：未取得序列号的可靠识别值，暂不能核对该项；不据此认定材料缺失或存在风险';
  const result = {
    materials: [{ material_id: 'component', file_name: '组件铭牌.png' }],
    material_reviews: [{ material_id: 'component', action: 'warning', triggered_rule_ids: ['DOC-COMPARISON-FIELD-UNAVAILABLE-001'], reasons: [notice], finding_ids: [] }],
    decision: { warnings: [notice], reasons: [notice] },
  };
  assert.equal(progress.deriveInsights(result, { status: 'done', reviews: [] }, 3).risks.length, 0);
  assert.equal(progress.deriveInsights(result, { status: 'done', reviews: [] }, 6).risks.filter(item => item.detail === notice).length, 0);
});

test('备案提前拒保保留整单建议，中间模块显示跳过', () => {
  const result = {
    processing_trace: [
      trace('material_parse'), trace('ocr'), trace('filing_rules'),
      trace('vision', 'skipped'), trace('ocr', 'skipped'),
      trace('installation_group', 'skipped'), trace('completeness', 'skipped'),
      trace('geometry_group', 'skipped'), trace('decision'),
    ],
    stage_reviews: [{ stage: 'filing', action: 'recommend_reject', terminates_run: true }],
    pre_catastrophe_outcome: 'recommend_reject',
  };
  assert.deepEqual(progress.derive(result).map(item => item.status), [
    'done', 'stopped', 'skipped', 'skipped', 'skipped', 'skipped', 'done',
  ]);
  assert.equal(progress.traceGroups(result)[1].filter(item => item.step === 'ocr').length, 1);
  assert.equal(progress.traceGroups(result)[3].filter(item => item.step === 'ocr').length, 1);
});

test('完整链路按真实步骤合并为七个业务模块', () => {
  const result = {
    processing_trace: [
      'material_parse', 'ocr', 'filing_rules', 'installation_group',
      'installation_rules', 'vision', 'ocr', 'rule_engine', 'completeness',
      'geometry_group', 'equipment_inventory', 'component_lookup',
      'weather_lookup', 'catastrophe_assessment', 'decision',
    ].map(step => trace(step)),
    stage_reviews: [], pre_catastrophe_outcome: 'continue',
  };
  assert.deepEqual(progress.derive(result).map(item => item.status), Array(7).fill('done'));
  assert.equal(progress.traceGroups(result)[5].length, 5);
});

test('安装类型拒保显示备案已完成、安装停止、后续跳过', () => {
  const result = {
    processing_trace: [
      trace('material_parse'), trace('ocr'), trace('filing_rules'),
      trace('installation_group'), trace('installation_rules'),
      trace('vision', 'skipped'), trace('completeness', 'skipped'),
      trace('geometry_group', 'skipped'), trace('decision'),
    ],
    stage_reviews: [{ stage: 'installation_type', action: 'recommend_reject', terminates_run: true }],
    pre_catastrophe_outcome: 'recommend_reject',
  };
  assert.deepEqual(progress.derive(result).map(item => item.status), [
    'done', 'done', 'stopped', 'skipped', 'skipped', 'skipped', 'done',
  ]);
});

test('位置冲突在前置核验提示复核，整单建议直接容纳核保报告', () => {
  const result = {
    case_id: 'location-review',
    location_assessment: { status: 'conflict', address_photo_conflict: true, notes: ['备案地址转换后的经纬度与照片相距 1697 公里'], evidence: [] },
    processing_trace: [trace('rule_engine'), trace('decision')],
    stage_reviews: [{ stage: 'location', action: 'manual_review', terminates_run: true, rule_ids: ['LOC-CONFLICT-001'] }],
    pre_catastrophe_outcome: 'manual_review', decision: { decision: 'manual_review' },
  };
  assert.equal(progress.derive(result)[4].status, 'attention');
  assert.equal(progress.deriveInsights(result, progress.derive(result)[4], 4).risks[0].tone, 'pending');
  class Element {
    constructor(tag) { this.tag = tag; this.children = []; this.attrs = {}; }
    append(...children) { this.children.push(...children); }
    replaceChildren(...children) { this.children = [...children]; }
    get firstChild() { return this.children[0]; }
    setAttribute(name, value) { this.attrs[name] = value; }
    addEventListener() {}
  }
  const originalDocument = global.document;
  global.document = { createElement: tag => new Element(tag) };
  try {
    const root = new Element('root');
    let invoked = 0;
    progress.render(root, { result, selected: 6, renderReport: target => { invoked++; target.append(new Element('report')); } });
    assert.equal(invoked, 1);
    const panel = root.children[0].children.find(item => item.tag === 'div' && item.children.some(child => child.tag === 'div' && child.children.some(grandchild => grandchild.tag === 'report')));
    assert.ok(panel, '报告应出现在整单建议节点内');
  } finally { global.document = originalDocument; }
});

test('风雪量化风险合为一项且不再显示冰雹缺口', () => {
  const result = { catastrophe_assessment: { comparisons: [
    { hazard: 'wind', status: 'insufficient', reason: '风荷载资料不足' },
    { hazard: 'hail', status: 'insufficient', reason: '抗雹参数不足' },
    { hazard: 'snow', status: 'insufficient', reason: '雪荷载资料不足' },
  ] } };
  const risks = progress.deriveInsights(result, { status: 'done', reviews: [] }, 5).risks;
  assert.equal(risks.length, 1);
  assert.equal(risks[0].title, '抗灾能力量化筛查');
  assert.match(risks[0].detail, /风灾/);
  assert.doesNotMatch(risks[0].detail, /雹灾|抗雹/);
  assert.match(risks[0].detail, /雪灾/);
});

test('单图提示不借用组图安装类型的标记作为证据', () => {
  const dateWarning = '拟起保日期未提供，15日时效暂无法核验';
  const result = {
    materials: [{ material_id: 'support', file_name: '支架节点.png' }],
    findings: [{ finding_id: 'installation', material_id: 'support', category: 'installation_type', label: '安装类型', evidence_text: '车棚顶安装类型', detection_status: 'detected' }],
    material_reviews: [{ material_id: 'support', action: 'warning', reasons: [dateWarning], finding_ids: ['installation'], triggered_rule_ids: ['IMG-WATERMARK-DATE-001'] }],
  };
  const singleRisks = progress.deriveInsights(result, { status: 'done', reviews: [] }, 3).risks;
  assert.equal(singleRisks.length, 1);
  assert.ok(!singleRisks[0].evidence.some(item => item.label === '安装类型'));
});

test('进度跨越中间阶段时不误报这些阶段已完成', () => {
  const job = { status: 'running', progress_percent: 95, current_step: '正在汇总综合核保意见' };
  assert.equal(progress.liveIndex(job), 6);
  assert.deepEqual(progress.derive(null, job, [0, 1]).map(item => item.status), [
    'done', 'done', 'waiting', 'waiting', 'waiting', 'waiting', 'active',
  ]);
});

test('旧案件没有保存前置步骤时显示未记录，不冒充跳过或待运行', () => {
  const result = {
    processing_trace: [trace('vision'), trace('rule_engine'), trace('decision')],
    stage_reviews: [],
  };
  assert.deepEqual(progress.derive(result).slice(0, 3).map(item => item.status), [
    'unrecorded', 'unrecorded', 'unrecorded',
  ]);
});

test('备案拒保提示关联具体字段、违规片段和原件位置', () => {
  const result = {
    materials: [{ material_id: 'm1', file_name: '备案证.png', category: 'filing_certificate' }],
    ocr_fields: [{
      field_id: 'f1', material_id: 'm1', field_name: 'project_name', page_number: 1,
      raw_value: '演示县集中式光伏发电项目',
      filing_assessment: {
        status: 'confirmed', reason: '项目名称明确含集中式', evidence_quote: '集中式',
        evidence_bbox: { x_min: 0.33, y_min: 0.27, x_max: 0.41, y_max: 0.32 },
      },
    }],
    stage_reviews: [{
      stage: 'filing', action: 'recommend_reject', terminates_run: true,
      rule_ids: ['DOC-FILING-EXCLUDED-001'], ocr_field_ids: ['f1'],
    }],
    processing_trace: [trace('ocr'), trace('filing_rules')],
  };
  const module = progress.derive(result)[1];
  const { risks } = progress.deriveInsights(result, module, 1);
  assert.equal(risks.length, 1);
  assert.equal(risks[0].tone, 'reject');
  assert.deepEqual(risks[0].ruleIds, ['DOC-FILING-EXCLUDED-001']);
  assert.equal(risks[0].evidence[0].source, '备案证.png');
  assert.equal(risks[0].evidence[0].fragment, '集中式');
  assert.deepEqual(risks[0].evidence[0].bbox, result.ocr_fields[0].filing_assessment.evidence_bbox);
});

test('提前拒保后的跳过节点不虚构风险或证据', () => {
  const result = {
    stage_reviews: [{ stage: 'filing', action: 'recommend_reject', terminates_run: true }],
    processing_trace: [trace('filing_rules'), trace('vision', 'skipped'), trace('decision')],
    findings: [{ finding_id: 'old', detection_status: 'detected', label: '不应出现在跳过节点' }],
  };
  const module = progress.derive(result)[3];
  assert.equal(module.status, 'skipped');
  assert.deepEqual(progress.deriveInsights(result, module, 3), { risks: [], contextEvidence: [] });
});

test('整单提示区分新增提醒和引用前序拒保依据', () => {
  const result = {
    project: { proposed_start_date: null },
    materials: [{ material_id: 'm1', file_name: '备案证.png' }],
    ocr_fields: [{ field_id: 'f1', material_id: 'm1', field_name: 'project_name', raw_value: '集中式光伏项目' }],
    stage_reviews: [{ stage: 'filing', action: 'recommend_reject', reasons: ['备案项目禁投'], ocr_field_ids: ['f1'] }],
    decision: { warnings: ['拟起保日期未填写'], reasons: ['备案项目禁投'], decisive_rule_ids: ['DOC-FILING-EXCLUDED-001'] },
    processing_trace: [trace('filing_rules'), trace('decision')],
  };
  const { risks } = progress.deriveInsights(result, progress.derive(result)[6], 6);
  assert.deepEqual(risks.map(item => item.tone), ['warning', 'inherited']);
  assert.equal(risks[1].evidence[0].source, '备案证.png');
});

test('15日时效提示关联照片水印日期的定位框', () => {
  const box = { x_min: .75, y_min: .95, x_max: .83, y_max: .99, coordinate_space: 'normalized_0_1' };
  const result = {
    project: { proposed_start_date: null },
    materials: [{ material_id: 'photo', file_name: '现场全景.png' }],
    ocr_fields: [{ field_id: 'date', material_id: 'photo', field_name: 'watermark_date', raw_value: '2026-10-05', value_status: 'extracted', bbox: box }],
    decision: { warnings: ['拟起保日期未提供，15日时效暂无法核验'], reasons: [] },
  };
  const { risks } = progress.deriveInsights(result, { status: 'done', reviews: [] }, 6);
  assert.equal(risks[0].evidence[0].source, '现场全景.png');
  assert.deepEqual(risks[0].evidence[0].bbox, box);
});

test('单图事实类识别和 info 级别发现不冒充风险提示', () => {
  const result = {
    processing_trace: [trace('vision')], stage_reviews: [],
    materials: [{ material_id: 'm1', file_name: '全景.png' }],
    findings: [
      { finding_id: 'a', material_id: 'm1', category: 'installation_type', detection_status: 'detected', label: '屋顶式' },
      { finding_id: 'b', material_id: 'm1', category: 'monitoring_effective_coverage', detection_status: 'detected', label: '监控覆盖' },
      { finding_id: 'c', material_id: 'm1', category: 'minor_shading', detection_status: 'detected', severity: 'info', label: '普通画面事实', evidence_text: '画面可见' },
    ],
  };
  const { risks } = progress.deriveInsights(result, progress.derive(result)[3], 3);
  assert.deepEqual(risks, []);
});

test('可选并网材料和逆变器铭牌未提供时不进入缺材提示', () => {
  const result = {
    processing_trace: [trace('completeness')], stage_reviews: [],
    applicable_checks: [
      { area_id: 'project', key: 'grid', label: '并网或调度材料', required_now: false },
      { area_id: 'project', key: 'inverter', label: '逆变器铭牌', required_now: false },
      { area_id: 'project', key: 'filing', label: '备案证明', required_now: true },
    ],
    check_results: [
      { area_id: 'project', key: 'grid', status: 'missing', reason: '历史记录中的可选缺项' },
      { area_id: 'project', key: 'inverter', status: 'not_provided', reason: '可选材料未提供' },
      { area_id: 'project', key: 'filing', status: 'missing', reason: '未收到备案证明', customer_request: '补充备案证明' },
    ],
  };
  const { risks } = progress.deriveInsights(result, progress.derive(result)[4], 4);
  assert.deepEqual(risks.map(item => item.title), ['备案证明：缺少材料']);
});

test('安装拒保优先展示逐图场景证据及其定位框', () => {
  const bbox = { x_min: 0.12, y_min: 0.2, x_max: 0.77, y_max: 0.81 };
  const result = {
    processing_trace: [trace('installation_group'), trace('installation_rules')],
    materials: [{ material_id: 'm1', file_name: '水上光伏.jpg' }],
    installation_analysis: { areas: [{
      scene_type: 'floating', status: 'confirmed', material_ids: ['m1'],
      evidence_regions: [{ material_id: 'm1', bbox, description: '组件与水面承载关系清楚可见' }],
    }] },
    stage_reviews: [{ stage: 'installation_type', action: 'recommend_reject', material_ids: ['m1'], rule_ids: ['INSTALLATION-TYPE-001'], terminates_run: true }],
  };
  const { risks, contextEvidence } = progress.deriveInsights(result, progress.derive(result)[2], 2);
  assert.equal(risks.length, 1);
  assert.equal(risks[0].evidence.length, 1);
  assert.equal(risks[0].evidence[0].quote, '组件与水面承载关系清楚可见');
  assert.deepEqual(risks[0].evidence[0].bbox, bbox);
  assert.equal(contextEvidence.length, 1);
});

test('单图多个 OCR 字段和发现共用一张原图，各自定位可切换', () => {
  const evidence = [
    { materialId: 'm1', source: '全景.jpg', label: '水印经度', quote: '115.354092', bbox: { x_min: .1, y_min: .7, x_max: .3, y_max: .8 } },
    { materialId: 'm1', source: '全景.jpg', label: '水印纬度', quote: '35.856636', bbox: { x_min: .3, y_min: .7, x_max: .5, y_max: .8 } },
    { materialId: 'm1', source: '全景.jpg', label: '环境风险', quote: '画面显示厂房屋顶', bbox: null },
  ];
  assert.equal(progress.groupEvidence(evidence).length, 1);
  assert.equal(progress.groupEvidence(evidence)[0].items.length, 3);
  assert.equal(progress.groupEvidence([{ ...evidence[0], materialId: 'm2' }, evidence[0]]).length, 2);

  class Element {
    constructor(tag) { this.tag = tag; this.children = []; this.style = {}; this.attrs = {}; this.events = {}; }
    append(...children) { this.children.push(...children); }
    setAttribute(name, value) { this.attrs[name] = value; }
    addEventListener(name, handler) { this.events[name] = handler; }
  }
  const originalDocument = global.document;
  global.document = { createElement: tag => new Element(tag), createTextNode: text => ({ tag: '#text', text }) };
  try {
    const root = new Element('root');
    let sourceCalls = 0;
    progress.renderEvidence(root, evidence, id => { sourceCalls++; assert.equal(id, 'm1'); return { url: 'blob:original' }; });
    const walk = element => [element, ...(element.children || []).flatMap(walk)];
    const nodes = walk(root);
    assert.equal(sourceCalls, 1);
    assert.equal(nodes.filter(item => item.tag === 'article').length, 1);
    assert.equal(nodes.filter(item => item.tag === 'img').length, 1);
    const overlays = nodes.filter(item => item.className === 'ap-evidence-box');
    const controls = nodes.filter(item => item.tag === 'button');
    assert.equal(overlays.length, 2);
    assert.equal(controls.length, 2);
    assert.deepEqual(overlays.map(item => item.hidden), [false, true]);
    controls[1].events.click();
    assert.deepEqual(overlays.map(item => item.hidden), [true, false]);
    assert.equal(controls[1].attrs['aria-pressed'], 'true');
  } finally {
    global.document = originalDocument;
  }
});
