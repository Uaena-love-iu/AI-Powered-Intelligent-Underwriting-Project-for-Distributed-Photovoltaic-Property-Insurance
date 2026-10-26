const assert=require('node:assert/strict');
require('../../frontend/analysis-progress.js');
const {build}=require('../../frontend/underwriting-report.js');
const findings=[{finding_id:'a',material_id:'1',category:'agriculture_environment',detection_status:'detected'},{finding_id:'b',material_id:'1',category:'flammable_material',detection_status:'detected'},{finding_id:'c',material_id:'2',category:'agriculture_environment',detection_status:'uncertain'},{finding_id:'d',material_id:'3',category:'agriculture_environment',detection_status:'detected'},{finding_id:'e',material_id:'1',category:'installation_type',detection_status:'detected'}];
const result=build({findings,material_reviews:[{material_id:'1',finding_ids:['a','b','e'],action:'recommend_reject',triggered_rule_ids:['ENV-EXCLUDED-001'],missing_requirements:['补拍通道']},{material_id:'2',finding_ids:['c'],missing_requirements:['补拍通道']}],decision:{missing_requirements:['补拍通道','补充坐标']}});
assert.deepEqual(result.decisive.map(f=>f.finding_id),['a'],'Rejecting material must not turn every finding into a rejection basis');
assert.deepEqual(result.pending.map(f=>f.finding_id),['c'],'Uncertainty remains even when another image detects the same environment');
assert.deepEqual(result.risks.map(f=>f.finding_id),['b','d'],'An unreferenced detected environment is not a rule-confirmed rejection basis');
assert.deepEqual(result.observations.map(f=>f.finding_id),['e']);
assert.equal(result.tasks.length,2);assert.deepEqual(result.tasks[0].materials,['1','2'],'Merged requests retain all material references');
assert.equal(build({}).decisive.length,0);
const riskSplit=build({findings:[
 {finding_id:'clear',material_id:'m1',category:'minor_shading',detection_status:'detected',confidence:.93},
 {finding_id:'uncertain',material_id:'m2',category:'minor_shading',detection_status:'detected',confidence:.42},
]});
assert.deepEqual(riskSplit.risks.map(f=>f.finding_id),['clear']);
assert.deepEqual(riskSplit.pending.map(f=>f.finding_id),['uncertain']);
console.log('6 report aggregation checks passed');
const {screeningData}=require('../../frontend/underwriting-report.js');
const old=screeningData({catastrophe_assessment:{expected_loss_risk:'high'},component_profile:{wind_load_pa:300},weather_profile:{historical_max_wind_m_s:30}});
assert.equal(old.legacy,true);
assert.equal(old.rows[0].capacity,300);
assert.equal(old.rows[0].ratio,null,'Historical results must not be recomputed using current thresholds');
assert.equal(old.rows[0].status,'not_saved');
const fresh=screeningData({catastrophe_assessment:{comparisons:[{hazard:'wind',capacity:300,event:30,demand:551.7,ratio:.544,status:'calculated',risk:'critical'}]}});
assert.equal(fresh.legacy,false);assert.equal(fresh.calculated,1);
assert.equal(fresh.rows[0].ratio,.544,'Use persisted server calculation without reconstructing it');
assert.equal(screeningData({}).rows[0].status,'not_evaluated');
console.log('Persisted screening and historical compatibility checks passed');
const {decisiveEvidence,decisiveGroups,riskGroups}=require('../../frontend/underwriting-report.js');
const installationCase={
 materials:[{material_id:'m1',file_name:'俯拍.jpg'},{material_id:'m2',file_name:'近景.jpg'}],
 installation_analysis:{areas:[{area_id:'project',scene_type:'floating',status:'confirmed',evidence_regions:[
  {material_id:'m1',description:'阵列位于水面',bbox:null},
  {material_id:'m2',description:'支柱立于水中',bbox:{x_min:.1,y_min:.2,x_max:.8,y_max:.9,coordinate_space:'normalized_0_1'}},
 ]}]},
 stage_reviews:[{stage:'installation_type',action:'recommend_reject',material_ids:['m1','m2'],rule_ids:['INSTALLATION-TYPE-001'],reasons:['非屋顶或车棚']}],
};
assert.deepEqual(decisiveEvidence(installationCase).map(item=>[item.materialId,item.explanation]),[['m1','阵列位于水面'],['m2','支柱立于水中']]);
assert.equal(decisiveEvidence(installationCase)[1].bbox.x_min,.1);
assert.equal(decisiveEvidence(installationCase).length,2,'One picture must not gain unrelated explanations');
assert.equal(decisiveGroups(decisiveEvidence(installationCase)).length,1,'Same installation conclusion is one collapsible report item');
assert.deepEqual(riskGroups([
 {category:'shade',label:'遮挡',material_id:'m1'},
 {category:'shade',label:'遮挡',material_id:'m2'},
 {category:'cable',label:'线缆外露',material_id:'m3'},
]).map(group=>group.items.length),[2,1],'Repeated risks keep their distinct source photos in one group');
const filingCase={
 stage_reviews:[{stage:'filing',action:'recommend_reject',material_ids:['doc'],ocr_field_ids:['field-1']}],
 ocr_fields:[{field_id:'field-1',material_id:'doc',field_name:'project_name',raw_value:'集中式光伏项目',filing_assessment:{status:'confirmed',reason:'项目名称禁投',evidence_quote:'集中式',evidence_bbox:{x_min:.2,y_min:.3,x_max:.4,y_max:.35,coordinate_space:'normalized_0_1'}}}],
};
assert.deepEqual(decisiveEvidence(filingCase).map(item=>item.title),['项目名称']);
assert.equal(decisiveEvidence(filingCase)[0].fragment,'集中式');
assert.equal(decisiveEvidence(filingCase)[0].refId,'ocr:field-1');
const filingNameConflict={
 stage_reviews:[{stage:'filing',action:'recommend_reject',rule_ids:['DOC-FILING-NAME-CONFLICT-001'],material_ids:['doc'],ocr_field_ids:['field-2'],reasons:['备案项目名称与申报不一致']}],
 ocr_fields:[{field_id:'field-2',material_id:'doc',field_name:'project_name',raw_value:'另一个项目',filing_assessment:{status:'not_found',reason:'未见禁投名称'}}],
};
assert.equal(decisiveEvidence(filingNameConflict)[0].quote,'另一个项目','Reliable filing name conflicts retain their original OCR evidence');
console.log('Decisive evidence stays paired with its own source');
class FakeElement{
 constructor(tag){this.tag=tag;this.className='';this.children=[];this.style={};this.textContent='';}
 append(...children){for(const child of children){if(child instanceof FakeElement){if(child.parent)child.parent.children=child.parent.children.filter(item=>item!==child);child.parent=this;}this.children.push(child);}return this;}
 addEventListener(event,handler){(this.listeners??={})[event]=handler;}
 click(){return this.listeners?.click?.();}
 querySelector(tag){for(const child of this.children){if(child instanceof FakeElement){if(child.tag===tag)return child;const found=child.querySelector(tag);if(found)return found;}}return null;}
}
global.document={createElement:tag=>new FakeElement(tag),createTextNode:text=>String(text)};
const rendered=new FakeElement('root');
require('../../frontend/underwriting-report.js').render(rendered,{
 ...installationCase,pre_catastrophe_outcome:'recommend_reject',
 decision:{decision:'recommend_reject',warnings:[],decisive_rule_ids:['INSTALLATION-TYPE-001']},
},{resolveSource:id=>({url:'blob:'+id,pdf:false})});
const descendants=(node,cls)=>node instanceof FakeElement?[...(node.className.split(' ').includes(cls)?[node]:[]),...node.children.flatMap(child=>descendants(child,cls))]:[];
const words=node=>node instanceof FakeElement?node.textContent+node.children.map(words).join(''):String(node);
for(const decisionCode of ['accept','recommend_reject','request_more','manual_review','surcharge','conditional_accept']){
 const sample=new FakeElement('root');
 require('../../frontend/underwriting-report.js').render(sample,{decision:{decision:decisionCode,warnings:[]}});
 assert.equal(descendants(sample,'uw-decision')[0].className.includes('uw-decision--'+decisionCode),true,`The ${decisionCode} suggestion has its own visual state`);
}
for(const [decisionCode,expected] of [
 ['accept','建议承保（加费）'],
 ['conditional_accept','建议附条件承保（加费）'],
 ['request_more','需补充材料'],
]){
 const sample=new FakeElement('root');
 require('../../frontend/underwriting-report.js').render(sample,{
  decision:{decision:decisionCode,pricing_recommendation:'surcharge',warnings:[]},
 });
 assert.equal(words(descendants(sample,'uw-decision')[0]).includes(expected),true);
}
const cards=descendants(descendants(rendered,'uw-decision')[0],'uw-decisive-item');
assert.equal(descendants(rendered,'uw-priority').length,0,'核心依据已在结论卡片中，不再重复优先审阅区');
assert.equal(cards.length,2);
assert.ok(words(cards[0]).includes('阵列位于水面'));
assert.ok(!words(cards[0]).includes('支柱立于水中'));
assert.ok(words(cards[1]).includes('支柱立于水中'));
assert.deepEqual(cards.map(card=>descendants(card,'uw-image')[0].querySelector('img').src),['blob:m1','blob:m2']);
assert.deepEqual(cards.map(card=>descendants(card,'uw-box').length),[0,1],
 'Final report basis keeps the installation location only on its matching picture');
console.log('Rendered report pairs each explanation with its own picture');
const {basisEvidence}=require('../../frontend/underwriting-report.js');
assert.deepEqual(basisEvidence({text:'支柱立于水中',ref:'material:m2'},installationCase)
 .map(item=>item.bbox?.x_min),[.1],
 'The final report retrieves the saved installation box even without single-image findings');
const pendingFinding={finding_id:'roof-1',material_id:'roof-photo',label:'屋顶连接异常',
 detection_status:'not_detected',requires_manual_review:true,
 evidence_text:'可见螺栓紧固，但承重连接节点未展示',bbox:null};
assert.deepEqual(basisEvidence({text:'模型标记屋顶连接异常：可见螺栓紧固，但承重连接节点未展示',ref:'material:roof-photo'},
 {findings:[pendingFinding]}).map(item=>item.materialId),['roof-photo']);
const pendingReport=new FakeElement('root');
require('../../frontend/underwriting-report.js').render(pendingReport,{
 materials:[{material_id:'roof-photo',file_name:'屋顶连接.jpg'}],findings:[pendingFinding],
 stage_reviews:[{stage:'single_image',action:'manual_review',material_ids:['roof-photo'],
  reasons:['模型标记屋顶连接异常：可见螺栓紧固，但承重连接节点未展示']}],
 decision:{decision:'manual_review',warnings:[]},
},{resolveSource:id=>({url:'blob:'+id,pdf:false})});
const inline=descendants(pendingReport,'uw-key-basis-source');
assert.ok(inline.length>0,'关键依据应提供原位展开控件');
assert.equal(descendants(inline[0],'uw-image')[0].querySelector('img').src,'blob:roof-photo');
const locationReject={materials:[{material_id:'filing',file_name:'备案.pdf'},{material_id:'site',file_name:'现场.jpg'}],
 decision:{decision:'recommend_reject',warnings:[]},
 stage_reviews:[{stage:'location',action:'recommend_reject'}],
 location_assessment:{status:'conflict',address_photo_conflict:true,
  notes:['备案地址与现场照片相距约 1700 公里'],evidence:[
   {source:'现场照片水印',material_id:'site',longitude:113.1189,latitude:23.0345},
   {source:'备案材料',material_id:'filing',address:'吴忠市同心县豫海镇'},
  ]}};
assert.deepEqual(decisiveEvidence(locationReject).map(item=>item.materialId),['filing','site']);
const locationReport=new FakeElement('root');
require('../../frontend/underwriting-report.js').render(locationReport,locationReject,
 {resolveSource:id=>({url:'blob:'+id,pdf:id==='filing'})});
assert.ok(words(descendants(locationReport,'uw-decision')[0]).includes('建议拒保'));
assert.deepEqual(descendants(locationReport,'uw-key-basis-source').slice(0,2).map(item=>
  descendants(item,'uw-decisive-visual')[0].querySelector('iframe')?.src||
  descendants(item,'uw-image')[0].querySelector('img').src),['blob:filing','blob:site']);
const manualLocationReport=new FakeElement('root');
require('../../frontend/underwriting-report.js').render(manualLocationReport,{
 materials:[{material_id:'site',file_name:'现场.jpg'},{material_id:'site-two',file_name:'现场补充.jpg'},{material_id:'filing',file_name:'备案.pdf'}],
 decision:{decision:'manual_review',reasons:['项目位置待复核'],warnings:[]},
 location_assessment:{status:'uncertain',notes:['项目位置待复核'],evidence:[
  {source:'现场照片水印',material_id:'site',longitude:105.91146,latitude:36.97829,coordinate_system:'未标明'},
  {source:'现场照片水印',material_id:'site-two',longitude:105.91146,latitude:36.97829,coordinate_system:'未标明'},
  {source:'备案材料',material_id:'filing',address:'吴忠市同心县豫海镇',geocode_level:'乡镇',geocode_candidates:[{address:'宁夏回族自治区吴忠市同心县豫海镇',level:'乡镇',longitude:105.912878,latitude:36.980018}]},
 ]},
 installation_analysis:{areas:[{area_id:'project',scene_type:'carport_roof',evidence_regions:[
  {material_id:'site',description:'车棚光伏',bbox:{x_min:.1,y_min:.2,x_max:.9,y_max:.7,coordinate_space:'normalized_0_1'}},
 ]}]},
},{resolveSource:id=>({url:'blob:'+id,pdf:id==='filing'})});
assert.deepEqual(descendants(manualLocationReport,'uw-key-basis-source').flatMap(item=>
 descendants(item,'uw-decisive-visual').map(visual=>visual.querySelector('iframe')?.src||visual.querySelector('img')?.src)),
 ['blob:site','blob:filing'],'Position review shows each distinct location once');
assert.ok(words(descendants(manualLocationReport,'uw-key-basis')[0]).includes('约 230 米'));
assert.ok(words(descendants(manualLocationReport,'uw-key-basis')[0]).includes('不能直接套用 500 米'));
const screenedManualReport=new FakeElement('root');
require('../../frontend/underwriting-report.js').render(screenedManualReport,{
 decision:{decision:'manual_review',reasons:['风雪风险量化结果需复核'],warnings:[]},
 location_assessment:{status:'distance_screened',notes:['原始距离在 500 米内'],evidence:[
  {source:'现场照片水印',material_id:'site',longitude:105.91146,latitude:36.97829,coordinate_system:'未标明'},
  {source:'备案材料',material_id:'filing',address:'吴忠市同心县豫海镇',geocode_level:'乡镇',geocode_candidates:[{address:'宁夏回族自治区吴忠市同心县豫海镇',level:'乡镇',longitude:105.912878,latitude:36.980018}]},
 ]},
});
assert.ok(words(descendants(screenedManualReport,'uw-key-basis')[0]).includes('风雪风险量化结果需复核'));
assert.ok(!words(descendants(screenedManualReport,'uw-key-basis')[0]).includes('需核对备案与现场位置原件'));
const failedGeometryReport=new FakeElement('root');
require('../../frontend/underwriting-report.js').render(failedGeometryReport,{
 decision:{decision:'manual_review',decisive_rule_ids:['IMG-WATERMARK-DATE-001','CAT-FRONT-LOAD-SCREENING-REVIEW','SYS-PROVIDER-FAILURE'],reasons:['拟起保日期未提供，15日时效暂无法核验','风雪结果待评估'],warnings:[]},
 location_assessment:{status:'distance_screened',notes:[]},
 pv_load_screening_assessment:{status:'PARTIAL',label:'风雪初筛等级：待评估'},
 processing_trace:[{step:'geometry_group',status:'failed'}],
});
const geometryBasis=descendants(failedGeometryReport,'uw-key-basis');
assert.ok(words(geometryBasis[0]).includes('风雪初筛等级：待评估'));
assert.ok(geometryBasis.some(item=>words(item).includes('组件倾角、离地高度和地面粗糙度估计')));
assert.ok(geometryBasis.every(item=>!words(item).includes('拟起保日期未提供')));
assert.equal(descendants(manualLocationReport,'uw-installation-evidence').length,0,
 'Unrelated installation evidence is not a standalone section in a location review');
const combinedReport=new FakeElement('root');
require('../../frontend/underwriting-report.js').render(combinedReport,{
 materials:[{material_id:'roof',file_name:'屋顶.jpg'}],
 findings:[{finding_id:'risk',material_id:'roof',category:'minor_shading',label:'轻微遮挡',detection_status:'detected',confidence:.96,evidence_text:'局部树影'},
  {finding_id:'pending',material_id:'roof',category:'roof_connection_abnormal',label:'屋顶连接待核',detection_status:'uncertain',evidence_text:'承重点未展示'}],
 catastrophe_assessment:{factors:['风灾能力数据不足','雹灾能力数据不足']},
 decision:{decision:'manual_review',warnings:['风灾能力数据不足','雹灾能力数据不足']},
},{resolveSource:id=>({url:'blob:'+id,pdf:false})});
assert.equal(descendants(combinedReport,'uw-priority').length,0);
assert.equal(descendants(combinedReport,'uw-pending').length,0);
assert.equal(descendants(combinedReport,'uw-risk-section').length,1);
assert.equal(descendants(combinedReport,'uw-pending-trigger').length,1);
assert.equal(descendants(combinedReport,'uw-warning-item').length,1,'风雹雪量化提示合并为一张卡片');
descendants(combinedReport,'uw-warning-item')[0].click();
assert.ok(words(descendants(combinedReport,'uw-risk-dialog')[0]).includes('风灾能力数据不足'));
assert.ok(words(descendants(combinedReport,'uw-risk-dialog')[0]).includes('雹灾能力数据不足'));
const riskReport=new FakeElement('root');
require('../../frontend/underwriting-report.js').render(riskReport,{
 materials:[{material_id:'m1',file_name:'一.jpg'},{material_id:'m2',file_name:'二.jpg'}],
 findings:[
  {finding_id:'r1',material_id:'m1',category:'minor_shading',label:'轻微遮挡',detection_status:'detected',evidence_text:'左侧树影',bbox:{x_min:.1,y_min:.2,x_max:.3,y_max:.5,coordinate_space:'normalized_0_1'}},
  {finding_id:'r2',material_id:'m2',category:'minor_shading',label:'局部遮挡',detection_status:'detected',evidence_text:'右侧树影'},
 ],decision:{decision:'manual_review',warnings:[]},
},{resolveSource:id=>({url:'blob:'+id,pdf:false})});
assert.equal(descendants(riskReport,'uw-risk-group').length,1);
assert.equal(descendants(riskReport,'uw-risk-dialog').length,0);
descendants(riskReport,'uw-risk-group')[0].click();
assert.equal(descendants(riskReport,'uw-risk-dialog').length,1);
assert.equal(descendants(riskReport,'uw-decisive-item').length,2);
assert.equal(descendants(riskReport,'uw-box').length,1,'The matched risk picture retains its location marker');
assert.deepEqual(descendants(riskReport,'uw-decisive-item').map(card=>descendants(card,'uw-image')[0].querySelector('img').src),['blob:m1','blob:m2']);
console.log('Repeated risk evidence opens marked source pictures in one dialog');
const riskCheckCase={
 materials:[{material_id:'photo',file_name:'现场全景.jpg'}],
 findings:[{finding_id:'risk-photo',material_id:'photo',category:'severe_shading',label:'严重遮挡',detection_status:'detected',evidence_text:'阵列被树影遮挡',bbox:{x_min:.2,y_min:.2,x_max:.5,y_max:.6,coordinate_space:'normalized_0_1'}}],
 applicable_checks:[{area_id:'project',key:'panorama',label:'现场全景',required_now:true,applicability:'applicable',evidence_requirement:'核对阵列和周边环境'}],
 check_results:[{area_id:'project',key:'panorama',status:'valid',material_ids:['photo'],finding_ids:['risk-photo'],reason:'已有可读材料'}],
 decision:{decision:'manual_review',warnings:[]},
};
const riskCheckReport=new FakeElement('root');
require('../../frontend/underwriting-report.js').render(riskCheckReport,riskCheckCase,{resolveSource:()=>({url:'blob:photo',pdf:false})});
descendants(riskCheckReport,'uw-risk-group')[0].click();
const riskCheckDetail=descendants(riskCheckReport,'uw-risk-dialog')[0];
assert.equal(descendants(riskCheckDetail,'uw-box').length,1,'A discovered checklist risk remains with its marked picture after removing coverage rows');
assert.ok(words(riskCheckDetail).includes('现场全景.jpg'));
assert.equal(descendants(riskCheckReport,'uw-checks').length,0,'The main report does not repeat the full checklist');
console.log('Each discovered checklist risk remains in a marked source picture dialog');
const warningCase={
 materials:[{material_id:'photo',file_name:'现场照片.jpg'}],
 findings:[{finding_id:'notice',material_id:'photo',category:'minor_shading',label:'局部遮挡',detection_status:'detected',evidence_text:'右下角有树影',bbox:{x_min:.6,y_min:.6,x_max:.9,y_max:.9,coordinate_space:'normalized_0_1'}}],
 material_reviews:[{material_id:'photo',action:'warning',triggered_rule_ids:['IMG-FINDING-WARNING-001'],finding_ids:['notice'],reasons:['局部遮挡需提示'],missing_requirements:[],requires_manual_review:false}],
 stage_reviews:[{stage:'single_image',action:'manual_review',finding_ids:['notice'],material_ids:['photo'],pending_questions:['请核对现场遮挡范围']}],
 decision:{decision:'manual_review',warnings:['局部遮挡需提示','请核对现场遮挡范围','气象资料不足，尚不能定级']},
};
const warningReport=new FakeElement('root');
require('../../frontend/underwriting-report.js').render(warningReport,warningCase,{resolveSource:()=>({url:'blob:photo',pdf:false})});
const warningCards=descendants(warningReport,'uw-warning-item');
assert.equal(warningCards.length,1,'Finding warnings and linked stage warnings appear in the photo group only once');
assert.ok(words(descendants(warningReport,'uw-risk-group')[0]).includes('含待复核结果'));
descendants(warningReport,'uw-risk-group')[0].click();
assert.equal(descendants(descendants(warningReport,'uw-risk-dialog')[0],'uw-box').length,1,'The grouped risk retains its marked photo');
warningCards[0].click();
assert.ok(words(descendants(warningReport,'uw-risk-dialog')[1]).includes('没有可对应的已标记现场图片'),'Data-only warnings must not invent a photo marker');
console.log('Linked photo warnings are shown once and data-only warnings remain separate');

const mixedShade={
 materials:[{material_id:'front',file_name:'正面.jpg'},{material_id:'top',file_name:'俯拍.jpg'}],
 findings:[
  {finding_id:'shade-front',material_id:'front',category:'minor_shading',label:'轻微遮挡',detection_status:'uncertain',evidence_text:'左侧树枝遮挡待核',bbox:{x_min:.1,y_min:.1,x_max:.3,y_max:.4,coordinate_space:'normalized_0_1'}},
  {finding_id:'shade-top',material_id:'top',category:'minor_shading',label:'轻微遮挡检查',detection_status:'detected',evidence_text:'左侧组件局部树影',bbox:{x_min:.2,y_min:.2,x_max:.4,y_max:.5,coordinate_space:'normalized_0_1'}},
 ],
 material_reviews:[
  {material_id:'front',finding_ids:['shade-front'],reasons:['图片风险判断不确定，需复核：轻微遮挡；左侧树枝遮挡待核']},
  {material_id:'top',finding_ids:['shade-top'],reasons:['图片风险提示：轻微遮挡检查；左侧组件局部树影']},
 ],
 decision:{decision:'manual_review',warnings:['图片风险判断不确定，需复核：轻微遮挡；左侧树枝遮挡待核','图片风险提示：轻微遮挡检查；左侧组件局部树影']},
};
const mixedShadeReport=new FakeElement('root');
require('../../frontend/underwriting-report.js').render(mixedShadeReport,mixedShade,{resolveSource:id=>({url:'blob:'+id,pdf:false})});
assert.equal(descendants(mixedShadeReport,'uw-risk-group').length,1,'Risk and pending findings of one type share a group');
assert.equal(descendants(mixedShadeReport,'uw-warning-item').length,0,'The same descriptions do not recur as warnings');
descendants(mixedShadeReport,'uw-risk-group')[0].click();
assert.deepEqual(descendants(mixedShadeReport,'uw-risk-dialog')[0].children.filter(child=>child.className==='uw-decisive-item').map(card=>descendants(card,'uw-image')[0].querySelector('img').src),['blob:top','blob:front']);
assert.equal(descendants(descendants(mixedShadeReport,'uw-risk-dialog')[0],'uw-box').length,2);
console.log('Detected and uncertain shading photos share one group and keep both evidence boxes');
// Public synthetic fixture: never load private local case archives.
const savedShadeCase=structuredClone(mixedShade);
savedShadeCase.decision.warnings.push('测试：组件参数待确认','测试：检测记录待核实','测试：拟起保日期未提供','测试：地址待核对','测试：坐标待核对','测试：地图位置待核对');
const savedShadeReport=new FakeElement('root');
require('../../frontend/underwriting-report.js').render(savedShadeReport,savedShadeCase,{resolveSource:id=>({url:'blob:'+id,pdf:false})});
const savedShadeGroups=descendants(savedShadeReport,'uw-risk-group');
assert.equal(savedShadeGroups.length,1,'The saved case has one shading risk group');
assert.ok(words(savedShadeGroups[0]).includes('2 张照片'));
assert.ok(savedShadeGroups[0].className.includes('uw-shading-trigger'),'Shading group uses the red card style');
assert.equal(descendants(savedShadeReport,'uw-warning-item').length,4,'The two shading warnings disappear and three location notices share one card');
savedShadeGroups[0].click();
assert.equal(descendants(descendants(savedShadeReport,'uw-risk-dialog')[0],'uw-decisive-item').length,2);
console.log('Saved shading case shows two source photos in one risk group without repeated notices');

const stableShadeCase=structuredClone(mixedShade);
stableShadeCase.materials.forEach((material,index)=>Object.assign(material,{category:'panorama',capture_view:index===0?'front_level':'overhead'}));
stableShadeCase.findings.forEach(finding=>Object.assign(finding,{detection_status:'detected',confidence:.95}));
stableShadeCase.decision.decisive_rule_ids=['IMG-SHADING-MULTIVIEW-REVIEW-001'];
stableShadeCase.decision.warnings.push('未提供拟起保日期，暂不能核验照片拍摄时间是否符合 15 日要求。');
stableShadeCase.stage_reviews=[{stage:'single_image',action:'manual_review',material_ids:['front','top'],reasons:['图片风险判断不确定：遮挡范围待核','拟起保日期未提供，暂不能核验照片时效']}];
const stableShadeReport=new FakeElement('root');
require('../../frontend/underwriting-report.js').render(stableShadeReport,stableShadeCase,{resolveSource:id=>({url:'blob:'+id,pdf:false})});
const stableBasis=descendants(stableShadeReport,'uw-key-basis-item');
assert.equal(stableBasis.length,1,'Two photo findings and the cross-view rule form one key basis');
assert.ok(words(stableBasis[0]).includes('遮挡范围及实际影响需人工核实'));
assert.ok(!words(stableBasis[0]).includes('图片风险判断不确定'));
assert.ok(!words(stableBasis[0]).includes('拟起保日期未提供'));
assert.deepEqual(descendants(stableBasis[0],'uw-image').map(image=>image.querySelector('img').src),
 stableShadeCase.materials.filter(material=>material.category==='panorama').map(material=>'blob:'+material.material_id));
assert.equal(descendants(stableBasis[0],'uw-box').length,2,'The cross-view basis uses each finding’s own red location box');
assert.ok(descendants(stableBasis[0],'uw-box').every(box=>box.className.includes('pending')));
const stableNext=words(descendants(stableShadeReport,'uw-next-summary')[0]);
assert.ok(stableNext.includes('核对正面与俯拍遮挡标框'));
assert.ok(stableNext.includes('确认拟起保日期'));
console.log('Cross-view basis collapses repeated reasons and opens both marked source photos');

const dateWarning='未提供拟起保日期，暂不能核验照片拍摄时间是否符合 15 日要求。';
const dateCase={
 project:{proposed_start_date:null},
 materials:[{material_id:'support',file_name:'03_支架连接节点_模拟.png'}],
 findings:[{finding_id:'installation',material_id:'support',category:'installation_type',label:'安装类型',detection_status:'detected',evidence_text:'画面展示车棚顶安装类型',bbox:{x_min:.1,y_min:.1,x_max:.9,y_max:.9,coordinate_space:'normalized_0_1'}}],
 material_reviews:[{material_id:'support',reasons:[dateWarning],finding_ids:['installation']}],
 stage_reviews:[{stage:'completeness',material_ids:['support'],pending_questions:[dateWarning]}],
 decision:{decision:'manual_review',warnings:[dateWarning]},
};
const dateReport=new FakeElement('root');
require('../../frontend/underwriting-report.js').render(dateReport,dateCase,{resolveSource:()=>({url:'blob:support',pdf:false})});
descendants(dateReport,'uw-warning-item')[0].click();
const dateDialog=descendants(dateReport,'uw-risk-dialog')[0];
assert.ok(words(dateDialog).includes('拟起保日期'));
assert.ok(!words(dateDialog).includes('安装类型'),'The 15-day warning cannot borrow installation findings');
assert.equal(descendants(dateDialog,'uw-decisive-visual').length,0,'No unrelated installation photo is shown');
console.log('Missing start date warning remains tied to project dates, not installation pictures');

const datedPhotoCase={...dateCase,
 ocr_fields:[{field_id:'shot-date',material_id:'support',field_name:'watermark_date',raw_value:'2026-10-05',normalized_value:'2026-10-05',value_status:'extracted',bbox:{x_min:.75,y_min:.95,x_max:.83,y_max:.99,coordinate_space:'normalized_0_1'}}],
 material_reviews:[{material_id:'support',triggered_rule_ids:['IMG-WATERMARK-DATE-001'],reasons:[dateWarning],ocr_field_ids:['shot-date'],finding_ids:['installation']}],
};
const datedPhotoReport=new FakeElement('root');
require('../../frontend/underwriting-report.js').render(datedPhotoReport,datedPhotoCase,{resolveSource:()=>({url:'blob:support',pdf:false})});
descendants(datedPhotoReport,'uw-warning-item')[0].click();
const datedPhotoDialog=descendants(datedPhotoReport,'uw-risk-dialog')[0];
assert.equal(descendants(datedPhotoDialog,'uw-box').length,1,'The date warning frames the captured date when OCR saved its coordinates');
assert.ok(descendants(datedPhotoDialog,'uw-box')[0].style.cssText.includes('left:74.4%'));
assert.ok(words(datedPhotoDialog).includes('2026-10-05'));
assert.ok(!words(datedPhotoDialog).includes('安装类型'),'The date frame must not borrow a scene finding');
console.log('The 15-day warning opens the photographed date with its own red evidence frame');

const watermarkCase={
 materials:[{material_id:'support',file_name:'支架节点.png'}],
 findings:[{finding_id:'installation',material_id:'support',category:'installation_type',label:'安装类型',detection_status:'detected',evidence_text:'图上可见安装类型；水印不完整',bbox:{x_min:.1,y_min:.1,x_max:.9,y_max:.9,coordinate_space:'normalized_0_1'}}],
 material_reviews:[{material_id:'support',reasons:['水印不完整'],finding_ids:['installation']}],
 decision:{decision:'manual_review',warnings:['水印不完整']},
};
const watermarkReport=new FakeElement('root');
require('../../frontend/underwriting-report.js').render(watermarkReport,watermarkCase,{resolveSource:()=>({url:'blob:support',pdf:false})});
descendants(watermarkReport,'uw-warning-item')[0].click();
const watermarkDialog=descendants(watermarkReport,'uw-risk-dialog')[0];
assert.ok(!words(watermarkDialog).includes('安装类型'),'Shared free-form wording is not proof that the installation finding supports the watermark warning');
assert.equal(descendants(watermarkDialog,'uw-box').length,0,'An unrelated installation marker is not reused for a watermark warning');

const comparisonNotice='交叉核对范围提示：未取得序列号的可靠识别值，暂不能核对该项；不据此认定材料缺失或存在风险';
const comparisonCase={
 materials:[{material_id:'component',file_name:'组件铭牌.png'}],
 findings:[],
 applicable_checks:[{area_id:'project',key:'component',label:'组件铭牌',required_now:true,applicability:'applicable',evidence_requirement:'核对型号和功率'}],
 check_results:[{area_id:'project',key:'component',status:'valid',material_ids:['component'],reason:'材料可核'}],
 material_reviews:[{material_id:'component',action:'warning',triggered_rule_ids:['DOC-COMPARISON-FIELD-UNAVAILABLE-001'],finding_ids:[],ocr_field_ids:[],reasons:[comparisonNotice],missing_requirements:[],requires_manual_review:false}],
 decision:{decision:'accept',warnings:[comparisonNotice],decisive_rule_ids:['DOC-COMPARISON-FIELD-UNAVAILABLE-001']},
};
assert.equal(require('../../frontend/underwriting-report.js').buildReportView(comparisonCase).coverage[0].state,'evidence_ready');
const unrelatedFinding={...comparisonCase,findings:[{finding_id:'unrelated',material_id:'component',category:'flammable_material',detection_status:'detected'}],check_results:[{...comparisonCase.check_results[0],finding_ids:['unrelated']}]};
assert.equal(require('../../frontend/underwriting-report.js').buildReportView(unrelatedFinding).coverage[0].state,'evidence_ready','A finding from another check must not turn the nameplate red');
const comparisonReport=new FakeElement('root');
require('../../frontend/underwriting-report.js').render(comparisonReport,comparisonCase);
assert.ok(!words(comparisonReport).includes('核对范围提示'),'Non-actionable comparison detail stays out of the concise report');
assert.ok(!words(comparisonReport).includes('其他风险提示'));
console.log('Unavailable comparison fields remain informational in the report');

const ocrPendingCase={
 materials:[{material_id:'doc',file_name:'备案证.png'}],
 applicable_checks:[{area_id:'project',key:'filing',label:'备案证明',required_now:true,applicability:'applicable',evidence_requirement:'核对项目名称和地址'}],
 check_results:[{area_id:'project',key:'filing',status:'pending',material_ids:['doc'],reason:'自动识别失败或未覆盖检查事项，需内部重试或人工核验：insured_name'}],
 material_reviews:[{material_id:'doc',action:'warning',triggered_rule_ids:['SYS-OCR-FIELDS-PENDING-001'],finding_ids:[],ocr_field_ids:[],reasons:['以下字段未返回可靠识别结果，需内部重试或人工核验原件：insured_name']}],
 decision:{decision:'manual_review',reasons:['以下字段未返回可靠识别结果，需内部重试或人工核验原件：insured_name','以下字段未返回可靠识别结果，需内部重试或人工核验原件：serial_number','部分检查项仍待内部复核'],warnings:[]},
};
const ocrView=require('../../frontend/underwriting-report.js').buildReportView(ocrPendingCase);
assert.equal(ocrView.coverage[0].state,'undetermined','OCR failure is not a discovered site risk');
assert.equal(ocrView.basis.length,1,'Related OCR gaps form one business issue');
assert.ok(ocrView.basis[0].text.includes('被保险人名称、设备序列号'));
assert.ok(ocrView.actions.some(action=>action.owner==='system'&&action.text.includes('文字识别待重试：被保险人名称、设备序列号')));
const ocrReport=new FakeElement('root');
require('../../frontend/underwriting-report.js').render(ocrReport,ocrPendingCase,{resolveSource:()=>({url:'blob:doc',pdf:false})});
assert.ok(words(ocrReport).includes('被保险人名称'));
assert.ok(!words(ocrReport).includes('insured_name'));
assert.ok(words(ocrReport).includes('文字识别待核'));
assert.equal(descendants(ocrReport,'uw-checks').length,0);
assert.equal(descendants(ocrReport,'uw-counts').length,0,'The first screen must not show four owner counters');
assert.equal(descendants(ocrReport,'uw-owner').length,0,'This underwriter report must not display assignment badges');
const combinerView=require('../../frontend/underwriting-report.js').buildReportView({...ocrPendingCase,applicable_checks:[...ocrPendingCase.applicable_checks,{area_id:'project',key:'combiner',label:'汇流箱',applicability:'pending',required_now:true,reason:'尚未核实是否配置汇流箱'}]});
assert.ok(combinerView.actions.some(action=>action.text==='确认项目是否配置汇流箱'));
console.log('OCR field gaps use business language and an inconclusive check state');

const {missingBasis,warningRule}=require('../../frontend/underwriting-report.js');
const missingCase={
 applicable_checks:[
  {area_id:'documents',key:'filing',label:'备案证明',required_now:true,applicability:'applicable',evidence_requirement:'核对项目名称、建设内容'},
  {area_id:'equipment',key:'component',label:'组件铭牌',required_now:false,applicability:'applicable',evidence_requirement:'核对组件型号'},
 ],
 check_results:[
  {area_id:'documents',key:'filing',status:'missing',customer_request:'补充备案证明：清楚显示备案项目名称、地址和建设内容',rule_ids:['CHECK-EVIDENCE-MISSING-001']},
  {area_id:'equipment',key:'component',status:'missing',customer_request:'补充组件铭牌',rule_ids:['CHECK-EVIDENCE-MISSING-001']},
 ],
 package_assessment:{image_count:4,panorama_count:2,has_filing_certificate:false,has_front_level_panorama:false,has_overhead_panorama:false,missing_requirements:[]},
 pre_catastrophe_outcome:'request_more',
 stage_reviews:[{stage:'completeness',action:'request_more',terminates_run:false,customer_requests:['补充清晰水印信息：拍摄日期']}],
 processing_trace:[{step:'completeness',status:'success'},{step:'geometry_group',status:'skipped'},{step:'component_lookup',status:'skipped'},{step:'weather_lookup',status:'skipped'},{step:'catastrophe_assessment',status:'skipped'},{step:'decision',status:'success'}],
 material_reviews:[{material_id:'m1',missing_requirements:['补充清晰水印信息：拍摄日期'],triggered_rule_ids:['IMG-WATERMARK-DATE-001'],reasons:['水印识别不完整']}],
 decision:{decision:'request_more',decisive_rule_ids:['CHECK-EVIDENCE-MISSING-001'],missing_requirements:['补充清晰水印信息：拍摄日期'],warnings:['水印识别不完整'],rule_explanations:[{rule_id:'CHECK-EVIDENCE-MISSING-001',title:'必需材料缺失'},{rule_id:'IMG-WATERMARK-DATE-001',title:'拍摄日期检查'}]},
};
const gaps=missingBasis(missingCase);
assert.equal(gaps.length,2,'Package counts and unknown panorama views do not create missing-material requests');
assert.ok(gaps[0].blocked.includes('核对项目名称'));
assert.ok(!gaps.some(item=>item.name==='组件铭牌'));
assert.equal(gaps.find(item=>item.name==='带清晰拍摄日期水印的现场照片').ruleId,'IMG-WATERMARK-DATE-001');
const missingReport=new FakeElement('root');
require('../../frontend/underwriting-report.js').render(missingReport,missingCase);
assert.equal(descendants(missingReport,'uw-missing-item').length,2);
const missingSection=descendants(missingReport,'uw-decision')[0];
assert.equal(missingSection.tag,'section');
assert.ok(words(missingSection).includes('待补材料'));
assert.equal(descendants(missingSection,'uw-missing-item').length,2,'Blocking gaps stay inside the decision card');
assert.equal(descendants(missingSection,'uw-missing-detail').length,0,'No nested per-item toggles');
assert.ok(words(descendants(missingReport,'uw-missing-item')[0]).includes('无法完成'));
assert.equal(descendants(missingSection,'uw-missing-list').length,1,'Missing items use one numbered list');
assert.equal(descendants(missingReport,'uw-priority').length,0,'Missing items do not repeat in a separate evidence section');
assert.equal(warningRule('水印识别不完整',missingCase),'IMG-WATERMARK-DATE-001');
console.log('Missing material report pairs each required gap with blocked work and a related rule');
const view=require('../../frontend/underwriting-report.js').buildReportView({...missingCase,processing_trace:[{step:'weather_lookup',status:'failed'}]});
assert.ok(view.actions.some(action=>action.owner==='customer'&&action.text.includes('备案')));
assert.ok(view.actions.some(action=>action.owner==='system'&&action.text.includes('当地历史天气查询')));
assert.ok(!view.actions.some(action=>action.text.includes('weather_lookup')),'Internal step names do not reach the underwriter');
assert.equal(descendants(missingReport,'uw-actions').length,0,'The missing-material report does not repeat next steps in a second section');
assert.equal(descendants(missingReport,'uw-next-summary').length,0,'The missing-material card does not repeat the checklist');
assert.equal(descendants(missingReport,'uw-rule-details').length,0);
assert.equal(descendants(missingReport,'uw-audit').length,0);
assert.equal(descendants(missingReport,'uw-screening').length,0,'Missing material stops the screening section');
assert.equal(descendants(missingReport,'uw-related-rule').length,0,'The report does not show rule text in cards');
assert.ok(descendants(missingReport,'uw-pending-list').every(list=>list.children.every(item=>item.className.includes('uw-evidence')||item.className.includes('uw-warning-item'))),'Other pending matters render as cards');
assert.equal(descendants(missingReport,'uw-risk-notice-list').length,1,'Non-blocking warnings sit under risk notices');
assert.equal(descendants(missingReport,'uw-pending-list').length,0,'Missing data is not mislabeled as model uncertainty');
assert.ok(descendants(missingReport,'uw-progress-step').some(item=>item.className.includes('attention')&&words(item).includes('缺 2 项材料')),'The completeness node shows the missing material count');
assert.ok(descendants(missingReport,'uw-progress-step').some(item=>item.className.includes('attention')),'The flow marks the blocked completeness step');
assert.ok(descendants(missingReport,'uw-progress-step').some(item=>item.className.includes('skipped')),'The flow marks disaster screening as not executed');
const staleScreeningReport=new FakeElement('root');
require('../../frontend/underwriting-report.js').render(staleScreeningReport,{
 ...missingCase,
 catastrophe_assessment:{comparisons:[]},
 processing_trace:[...missingCase.processing_trace.filter(trace=>trace.step!=='catastrophe_assessment'),{step:'catastrophe_assessment',status:'success'}],
});
const staleScreeningStep=descendants(staleScreeningReport,'uw-progress-step').find(item=>words(item).includes('需重查'));
assert.ok(staleScreeningStep?.className.includes('attention'),'Historical screening is amber when missing material invalidates it');
assert.ok(words(staleScreeningStep).includes('!'),'Historical screening does not keep a green completion mark');
assert.equal(view.coverage.find(item=>item.label==='备案证明').state,'not_executed','Missing material means the check did not run');

async function testOptionalInterpretation() {
 const aiCase={materials:[{material_id:'m1',file_name:'全景.jpg'}],decision:{decision:'manual_review',reasons:['现场关系待核']}};
 const aiReport=new FakeElement('root');
 let finish,calls=0;
 global.fetch=url=>{assert.equal(url,'/api/v1/reports/interpret/stream');calls++;return new Promise(resolve=>{finish=resolve;});};
 require('../../frontend/underwriting-report.js').render(aiReport,aiCase,{interpretation:true,resolveSource:()=>({url:'blob:m1'})});
 assert.ok(words(aiReport).includes('现场关系待核'),'The core report renders before AI resolves');
 assert.equal(calls,0,'Rendering the report must not start AI');
 const children=aiReport.children[0].children;
 assert.ok(children.findIndex(child=>child.className.includes('uw-decision'))<children.findIndex(child=>child.className.includes('uw-ai-summary')),'The AI button follows the decision card');
 const button=descendants(aiReport,'uw-ai-button')[0];
 const panel=descendants(aiReport,'uw-ai-panel')[0];
 assert.equal(panel.hidden,true,'The summary starts collapsed');
 const run=button.click();
 assert.equal(calls,1,'AI starts only after a click');
 assert.equal(panel.hidden,false,'Generating opens the summary before the stream completes');
 assert.equal(button.textContent,'收起总结');
 const encoder=new TextEncoder();const raw='{"type":"token","text":"目前暂停自动判断，"}\n{"type":"token","text":"请核实现场关系。"}\n{"type":"done","cached":false}\n';
 const chunks=[raw.slice(0,18),raw.slice(18,52),raw.slice(52)].map(part=>encoder.encode(part));let index=0;
 finish({ok:true,body:{getReader:()=>({read:async()=>index<chunks.length?{value:chunks[index++],done:false}:{done:true}})}});
 await run;
 assert.ok(words(aiReport).includes('目前暂停自动判断，请核实现场关系。'));
 const refresh=descendants(aiReport,'uw-ai-refresh')[0];assert.equal(refresh.hidden,false,'The small regenerate control appears after the first summary');
 assert.equal(refresh.textContent,'↻');assert.equal(refresh.title,'重新生成总结');
 button.click();assert.equal(panel.hidden,true,'The summary can be collapsed');
 button.click();assert.equal(panel.hidden,false,'The saved summary can be reopened');
 assert.equal(calls,1,'Collapse and reopen do not request a new summary');
 const refreshedToken='{"type":"token","text":"重新生成后的摘要。"}\n';let refreshBody;
 let completeRefresh,finishRefreshStream,refreshCalls=0;
 global.fetch=(_url,options)=>{refreshCalls++;refreshBody=JSON.parse(options.body);return new Promise(resolve=>{completeRefresh=()=>{let readCount=0;resolve({ok:true,body:{getReader:()=>({read:()=>{
  readCount++;
  if(readCount===1)return Promise.resolve({value:encoder.encode(refreshedToken),done:false});
  if(readCount===2)return new Promise(resolveChunk=>{finishRefreshStream=resolveChunk;});
  return Promise.resolve({done:true});
 }})}});};});};
 const refreshing=refresh.click();
 assert.equal(refresh.disabled,true);assert.notEqual(button.disabled,true);
 assert.ok(words(aiReport).includes('目前暂停自动判断，请核实现场关系。'),'The previous summary stays visible while regenerating');
 button.click();assert.equal(panel.hidden,true,'The panel can be collapsed during regeneration');
 button.click();assert.equal(panel.hidden,false);
 refresh.click();assert.equal(refreshCalls,1,'Only one summary request runs at a time');
 completeRefresh();
 await new Promise(resolve=>setImmediate(resolve));
 assert.equal(descendants(aiReport,'uw-ai-draft')[0].textContent,'重新生成后的摘要。','Regeneration streams a visible draft');
 assert.equal(descendants(aiReport,'uw-ai-output')[0].textContent,'目前暂停自动判断，请核实现场关系。','The completed version remains until regeneration finishes');
 finishRefreshStream({value:encoder.encode('{"type":"done","cached":false}\n'),done:false});await refreshing;
 assert.equal(refreshBody.force_refresh,true,'Regeneration bypasses the saved summary');
 assert.ok(words(aiReport).includes('重新生成后的摘要。'));
 global.fetch=()=>Promise.reject(Error('refresh offline'));
 await refresh.click();
 assert.ok(words(aiReport).includes('重新生成后的摘要。'),'A failed regeneration keeps the last complete summary');
 assert.equal(descendants(aiReport,'uw-interpretation-point').length,0,'The summary is flowing prose rather than fixed headings');
 const streamingReport=new FakeElement('root');
 let finishStream;
 global.fetch=async()=>({ok:true,body:{getReader:()=>{let readCount=0;return {read:()=>{
  readCount++;
  if(readCount===1)return Promise.resolve({value:encoder.encode('{"type":"token","text":"已收到第一段。"}\n'),done:false});
  if(readCount===2)return new Promise(resolve=>{finishStream=resolve;});
  return Promise.resolve({done:true});
 }};}}});
 require('../../frontend/underwriting-report.js').render(streamingReport,{...aiCase},{interpretation:true});
 const streaming=descendants(streamingReport,'uw-ai-button')[0].click();
 await new Promise(resolve=>setImmediate(resolve));
 assert.equal(descendants(streamingReport,'uw-ai-output')[0].textContent,'已收到第一段。','Tokens become visible before the stream finishes');
 finishStream({value:encoder.encode('{"type":"done","cached":false}\n'),done:false});
 await streaming;
 const invalidReport=new FakeElement('root');
 const rejected='{"type":"token","text":"已经承保"}\n{"type":"error","message":"摘要未通过校验"}\n';let invalidRead=false;
 global.fetch=async()=>({ok:true,body:{getReader:()=>({read:async()=>invalidRead?{done:true}:(invalidRead=true,{value:encoder.encode(rejected),done:false})})}});
 require('../../frontend/underwriting-report.js').render(invalidReport,{...aiCase},{interpretation:true});
 await descendants(invalidReport,'uw-ai-button')[0].click();
 assert.ok(!words(invalidReport).includes('已经承保'),'Rejected streamed draft must disappear');
 const failedReport=new FakeElement('root');
 global.fetch=()=>Promise.reject(Error('offline'));
 require('../../frontend/underwriting-report.js').render(failedReport,{...aiCase},{interpretation:true});
 await descendants(failedReport,'uw-ai-button')[0].click();
 assert.ok(words(failedReport).includes('offline'));
 assert.ok(words(failedReport).includes('现场关系待核'),'AI failure does not remove the report');
 console.log('AI summary starts on click, streams after the decision, and fails independently');
}
testOptionalInterpretation().catch(error=>{console.error(error);process.exitCode=1;});

const delivered=JSON.parse(require('node:fs').readFileSync(require('node:path').resolve(__dirname,'../../examples/wind-snow/04_expected_assessment.json'),'utf8'));
const newScreening=screeningData({pv_load_screening_assessment:delivered});
assert.equal(newScreening.total,2);assert.equal(newScreening.calculated,2);assert.equal(newScreening.rows[0].demand,637.5);
const windSnowRoot=new FakeElement('root');
require('../../frontend/underwriting-report.js').render(windSnowRoot,{project:{},materials:[],findings:[],material_reviews:[],processing_trace:[{step:'front_load_screening',status:'success'}],pre_catastrophe_outcome:'continue',decision:{decision:'manual_review',reasons:[],warnings:[],missing_requirements:[]},pv_load_screening_assessment:delivered});
assert.ok(words(windSnowRoot).includes('637.5'));
assert.ok(words(windSnowRoot).includes('风雪风险量化'));
assert.ok(!words(windSnowRoot).includes('历史记录未保存逐项计算明细'));
const mappedComponentRoot=new FakeElement('root');
require('../../frontend/underwriting-report.js').render(mappedComponentRoot,{project:{component_model:'JAM72S20-450/MR'},materials:[],findings:[],material_reviews:[],processing_trace:[],pre_catastrophe_outcome:'continue',decision:{decision:'accept',reasons:[],warnings:[],missing_requirements:[]},pv_load_screening_assessment:delivered,component_module_result:{status:'parameters_mapped',profile:{component_model:'JAM72S20-450/MR',manufacturer:'JA Solar',ui_fields:{wind_load_pa:{label:'风荷载',value:2400,unit:'Pa',value_origin:'mechanical_mapping',status:'mechanical_mapped'}},mechanical_fields:{}}}});
assert.ok(words(mappedComponentRoot).includes('查询成功，已取得正反面机械载荷'));
assert.ok(words(mappedComponentRoot).includes('本次未取得厂家单独列示的风荷载、雪荷载字段'));
assert.ok(words(mappedComponentRoot).includes('风荷载（机械映射参考）'));
console.log('Delivered wind/snow output renders without legacy screening rows');
function renderWindSnowFixture(assessment){
 const root=new FakeElement('root');
 require('../../frontend/underwriting-report.js').render(root,{project:{},materials:[],findings:[],material_reviews:[],processing_trace:[],pre_catastrophe_outcome:'continue',decision:{decision:'manual_review',reasons:[],warnings:[],missing_requirements:[]},pv_load_screening_assessment:assessment});
 return descendants(root,'uw-risk-screen')[0];
}
const clearScreen=renderWindSnowFixture(structuredClone(delivered));
assert.ok(words(descendants(clearScreen,'uw-risk-verdict')[0]).includes('未发现超载提示'));
assert.equal(descendants(clearScreen,'uw-hazard-card').length,2);
assert.ok(words(clearScreen).includes('未评价背面风吸力'));
assert.ok(words(clearScreen).includes('待核实'));
assert.equal(descendants(clearScreen,'uw-risk-evidence')[0].open,undefined,'Evidence is collapsed by default');
const missingAssessment=structuredClone(delivered);missingAssessment.snow={reason:'未取得雪压',risk_level:'unknown'};
const missingScreen=renderWindSnowFixture(missingAssessment);
assert.ok(words(descendants(missingScreen,'uw-risk-verdict')[0]).includes('暂无法完整判断'));
assert.equal(descendants(missingScreen,'uw-risk-verdict')[0].className.includes('uw-risk-clear'),false);
const exceededAssessment=structuredClone(missingAssessment);exceededAssessment.wind.load_pa.max=6000;exceededAssessment.wind.capacity_pa=5400;
const exceededScreen=renderWindSnowFixture(exceededAssessment);
assert.ok(words(descendants(exceededScreen,'uw-risk-verdict')[0]).includes('发现荷载超出提示'),'Known exceedance stays prominent even when snow is missing');
assert.ok(words(exceededScreen).includes('超过标称载荷 11.1%'));
console.log('Underwriter summary distinguishes clear, missing and exceeded results and keeps evidence collapsed');
const scopedRoot=new FakeElement('root');
require('../../frontend/underwriting-report.js').render(scopedRoot,{
 project:{},materials:[],findings:[],material_reviews:[],processing_trace:[],
 decision:{decision:'manual_review',reasons:['本次为专项检查'],warnings:[]},
 ruleset_snapshot:{snapshot_id:'frozen-v1',scheme_name:'某公司专项',catalog_version:'v1.0',modified_from_scheme:true,
   selection:{scheme_version:2,scopes:{}},parameters:{watermark_days:30,environment_min_confidence:.8,material_profile:'demo_minimum'},
   rules:[{id:'wind_snow',status:'not_selected'}]},
 rule_execution:[{name:'风雪承载风险',status:'not_selected',reason:'未纳入本次检查'},
   {name:'组件表面损伤',status:'material_missing',reason:'缺少现场照片'}],
});
assert.ok(words(scopedRoot).includes('本次已调整，以快照为准'));
assert.ok(words(scopedRoot).includes('未选择'));
assert.ok(words(scopedRoot).includes('材料不足'));
assert.ok(words(scopedRoot).includes('frozen-v1'));
assert.equal(descendants(scopedRoot,'uw-risk-screen').length,0,'未选择风雪时不能再渲染风雪未完成的测算卡片');
console.log('Rule scope and snapshot are shown separately from underwriting results');

const manualReport=new FakeElement('root');
require('../../frontend/underwriting-report.js').render(manualReport,{case_id:'CASE-MANUAL',component_review:{review_id:'CP-TEST',applied:true,component_model:'TEST-600',reviewer_name:'测试人',model_reason:'逐字核对',reason:'补充规格书',parameters:{front_static_load_pa:{value:5.4,unit:'kPa',normalized_value:5400,normalized_unit:'Pa',load_basis:'test',source_material_id:'spec',document_title:'规格书',document_version:'V1',page:'2',quote:'front test',applicability:'四点夹持',installation_confirmed:false}}}});
assert.match(words(manualReport),/本案确认 5400 Pa/);assert.match(words(manualReport),/原始输入：5.4 kPa/);assert.match(words(manualReport),/现场适用性待核验/);assert.match(words(manualReport),/未写入共享参数库/);
console.log('Case-local parameter report preserves source, units and review scope');

const componentOnly=new FakeElement('root');
assert.doesNotThrow(()=>require('../../frontend/underwriting-report.js').render(componentOnly,{ruleset_snapshot:{snapshot_id:'component-only',rules:[],parameters:{},selection:{},context:{}},component_profile:{component_model:'TEST-600'},catastrophe_assessment:null,component_module_result:{status:'case_confirmed',profile:{component_model:'TEST-600',mechanical_fields:{front_static_load_pa:{label:'正面机械载荷',value:5400,unit:'Pa',status:'found',load_basis:'test'}}}}}));
assert.match(words(componentOnly),/5400 Pa/);
console.log('Component-only record export works when the weather step is unselected');

const strategyReport=new FakeElement('root');
require('../../frontend/underwriting-report.js').render(strategyReport,{decision:{decision:'accept'},strategy_assessment:{scheme_snapshot:{id:'S1',name:'演示策略',company:'模拟公司',version:1,demo:true,basis:'测试依据',approved_by:'测试人',valid_from:'2026-01-01',expires_at:'2028-01-01',selected_at:'2026-10-22'},facts:{start_date:'2027-01-15',wind:'high',snow:'unknown',evidence:'unknown'},original_ai_decision:'accept',effective_suggestion:'manual_review',attention_level:'high',pricing:{notice:'未配置费率'},reasons:['最高优先级动作冲突'],selected_ids:['winter'],rule_results:[{rule_id:'winter',name:'冬季风灾',status:'matched',priority:100,action:'surcharge',observed_value:'high',reason:'模拟规则',evidence_ref:'pv_load_screening_assessment.wind',missing:[]}]}});
assert.match(words(strategyReport),/承保策略处理建议 · 演示方案/);
assert.match(words(strategyReport),/原 AI 建议：原 AI 建议通过；策略处理建议：人工复核/);
assert.match(words(strategyReport),/2027-01-15/);assert.match(words(strategyReport),/最高优先级动作冲突/);assert.match(words(strategyReport),/未配置费率/);
console.log('Strategy report keeps AI facts, business dates, conflicts and policy evidence distinct');
const coverageReport=new FakeElement('root');
require('../../frontend/underwriting-report.js').render(coverageReport,{case_id:'COVERAGE-TEST',materials:[{material_id:'a',file_name:'连接照片.png'}],evidence_coverage:{version:'1',notice:'缺材不等于故意隐瞒',customer_requests:['拍清锚固部位'],review_questions:['模型漏答，内部核验'],duplicates:[{material_id:'copy',duplicate_of:'a'}],rows:[{rule_id:'connection',name:'连接与锚固',area_id:'屋面A',status:'insufficient',reason:'逐部位检查',material_ids:['a'],targets:[{label:'锚固点',status:'insufficient',reason:'被遮挡',observations:[{material_id:'a',status:'occluded',evidence:'节点被遮挡'}]}]},{rule_id:'surface',name:'组件表面',area_id:'project',status:'not_selected',reason:'未纳入检查',targets:[],material_ids:[]}]}});
assert.match(words(coverageReport),/客户补材 1 项 · 内部核验 1 项/);
assert.match(words(coverageReport),/拍清锚固部位/);
assert.match(words(coverageReport),/连接照片.png：被遮挡/);
assert.match(words(coverageReport),/未选择，未检查/);
assert.match(words(coverageReport),/不增加关键部位覆盖/);
assert.match(words(coverageReport),/内部核验事项（不直接要求客户重拍）/);
console.log('Evidence coverage separates retakes, internal review, duplicates and unselected rules');

const coverageHero=new FakeElement('root');
const coverageCase={decision:{decision:'request_more',missing_requirements:['连接与锚固（roof_A）—拍清固定部位'],reasons:[],warnings:[]},evidence_coverage:{version:'1',notice:'模拟',customer_requests:['连接与锚固（roof_A）—拍清固定部位'],review_questions:[],duplicates:[],rows:[{rule_id:'connection',name:'连接与锚固',area_id:'roof_A',status:'insufficient',reason:'关键部位缺失',targets:[],material_ids:['node'],customer_requests:['连接与锚固（roof_A）—拍清固定部位']}]}};
require('../../frontend/underwriting-report.js').render(coverageHero,coverageCase);
assert.match(words(coverageHero),/待补材料 · 1/);
assert.equal(require('../../frontend/underwriting-report.js').buildReportView(coverageCase).actions.filter(a=>a.owner==='customer').length,1);
