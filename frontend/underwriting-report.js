/* Presentation only: the server remains the source of underwriting decisions. */
(function(global){
const decisions={accept:'建议承保',recommend_reject:'建议拒保',surcharge:'建议承保（加费）',conditional_accept:'建议附条件承保',request_more:'需补充材料',manual_review:'需人工复核'};
const priceApplies=decision=>['accept','conditional_accept','surcharge'].includes(decision?.decision)&&(decision.pricing_recommendation==='surcharge'||decision.decision==='surcharge');
const decisionLabel=decision=>(decisions[decision?.decision]||'尚未生成建议')+(priceApplies(decision)&&decision.decision!=='surcharge'?'（加费）':'');
const environment=new Set(['agriculture_environment','forest_environment','livestock_environment','fishery_environment','water_adjacent_environment','tidal_flat_environment','mountain_environment','desertification_environment']);
const rejectionRules=new Set(['ENV-EXCLUDED-001','IMG-SHADING-SEVERE-001','ELEC-UNPROTECTED-CABLE-REJECT-001','DOC-FILING-EXCLUDED-001','DOC-FILING-NAME-CONFLICT-001','INSTALLATION-TYPE-001','SCOPE-INSTALLATION-001']);
const neutral=new Set(['installation_type','cleanroom','monitoring_effective_coverage']);
const processingStepNames={ocr:'材料文字识别',vision:'现场照片检查',geometry_group:'组件倾角、离地高度和地面粗糙度估计',equipment_inventory:'设备清单检查',component_lookup:'组件资料查询',weather_lookup:'当地历史天气查询',front_load_screening:'风雪风险量化',catastrophe_assessment:'风雪检查',map_review:'项目位置核对',rule_engine:'核保规则检查',decision:'核保建议生成'};
const fieldNames={insured_name:'被保险人名称',serial_number:'设备序列号',document_title:'文件标题',document_type:'文件类型',insured_address:'被保险地址',project_name:'项目名称',construction_content:'建设内容',project_entity:'项目单位',site_address:'项目地址',component_model:'组件型号',inverter_model:'逆变器型号',rated_power_w:'额定功率',grid_connection_date:'并网日期',grounding_resistance_ohm:'接地电阻',inspection_date:'检测日期',inspection_result:'检测结论',monitoring_present:'监控配置',watermark_present:'照片拍摄水印',watermark_date:'照片拍摄日期',watermark_time:'照片拍摄时间',watermark_longitude:'照片水印经度',watermark_latitude:'照片水印纬度',watermark_address:'照片水印地址',watermark_coordinate_system:'照片水印坐标系',shot_date:'照片拍摄日期',single_image:'逐张照片检查',filing:'备案材料检查',installation_type:'安装类型检查',installation_scope:'安装范围检查',aisle_group:'组图检修通道检查',completeness:'材料完整性检查',location:'项目位置检查',valid:'材料可核',pending:'待核实',unassessed:'未返回识别结果',missing:'未收到',not_provided:'未提供',unusable:'材料不可核',not_applicable:'不适用',applicable:'适用',warning:'提示',request_more:'补充材料',manual_review:'人工复核',recommend_reject:'建议拒保',conditional:'附条件承保',surcharge:'建议加费',pass:'可核'};
const businessText=value=>String(value??'').replace(/\b[a-z][a-z0-9_]*\b/g,word=>fieldNames[word]||(word.includes('_')?'其他待核字段':word));
const watermarkDays=result=>result?.ruleset_snapshot?.parameters?.watermark_days??15;
function warningText(value){
 const text=businessText(value);
 if(text.startsWith('拟起保日期未提供'))return `未提供拟起保日期，暂不能核验照片拍摄时间是否符合 ${text.match(/(\d+)日时效/)?.[1]||15} 日要求。`;
 if(text.includes('地面粗糙度资料不足')||text.includes('地面粗糙度仍未知'))return '现有照片不足以判断项目周边地形暴露情况，抗风筛查范围受限。';
 if(text.startsWith('筛查资料缺口：')||text.startsWith('人工复核事项：')||text.startsWith('安装参数适用性待确认：'))return '组件厂家参数尚未与现场安装条件核对，风雪筛查不能形成确定结论。';
 return text;
}
const checkRiskCategories={filing:new Set(),grid:new Set(),component:new Set(),panorama:new Set([...environment,'severe_shading','minor_shading','aisle_obstruction']),support:new Set(['roof_connection_abnormal','aisle_obstruction']),edge:new Set(['missing_parapet_or_guardrail','drainage_abnormal','aisle_obstruction']),workshop:new Set(['flammable_material','hazardous_material','dangerous_process','fire_protection_absent']),grounding:new Set(['electrical_grounding_abnormal','unprotected_cable']),combiner:new Set(['combiner_box_seal_abnormal','combiner_box_fuse_abnormal','surge_protector_abnormal']),inverter:new Set(['inverter_abnormal'])};
const n=(tag,cls,text)=>{const el=document.createElement(tag);el.className=cls||'';if(text!=null)el.textContent=text;return el;};
const refDomId=ref=>'uw-ref-'+encodeURIComponent(ref);
function paddedEvidenceBox(box,padX=.006,padY=.008){
 if(!box||box.coordinate_space!=='normalized_0_1'||![box.x_min,box.y_min,box.x_max,box.y_max].every(x=>Number.isFinite(x)&&x>=0&&x<=1)||box.x_max<=box.x_min||box.y_max<=box.y_min)return null;
 const x_min=Math.max(0,box.x_min-padX),y_min=Math.max(0,box.y_min-padY);
 const x_max=Math.min(1,box.x_max+padX),y_max=Math.min(1,box.y_max+padY);
 return {x_min,y_min,x_max,y_max};
}
function build(result){
 const reviews=result.material_reviews||[], findings=result.findings||[];
 const reviewFor=f=>reviews.filter(r=>r.material_id===f.material_id&&(r.finding_ids||[]).includes(f.finding_id));
 const rejectRule=f=>environment.has(f.category)?'ENV-EXCLUDED-001':f.category==='severe_shading'?'IMG-SHADING-SEVERE-001':f.category==='unprotected_cable'?'ELEC-UNPROTECTED-CABLE-REJECT-001':null;
 const decisive=[], risks=[], pending=[], observations=[];
 for(const f of findings){const refs=reviewFor(f), rule=rejectRule(f);const item={...f,rules:[...new Set(refs.flatMap(r=>r.triggered_rule_ids||[]))]};
  if(f.detection_status==='detected'&&rule&&refs.some(r=>r.action==='recommend_reject'&&(r.triggered_rule_ids||[]).includes(rule))){item.rules=[rule];decisive.push(item);}
  else if(f.detection_status==='uncertain'||f.category==='image_quality'&&f.detection_status==='detected'||f.requires_manual_review&&f.detection_status==='not_detected'||f.detection_status==='detected'&&!neutral.has(f.category)&&(f.requires_manual_review||Number.isFinite(f.confidence)&&f.confidence<.65||environment.has(f.category)&&f.environment_relation==='uncertain'))pending.push(item);
  else if(f.detection_status==='detected'&&!neutral.has(f.category))risks.push(item);
  else observations.push(item);
 }
 const unique=xs=>[...new Set(xs.filter(Boolean))];
 const tasks=unique([...(result.decision?.missing_requirements||[]),...reviews.flatMap(r=>r.missing_requirements||[])]).map(text=>({text,materials:unique(reviews.filter(r=>(r.missing_requirements||[]).includes(text)).map(r=>r.material_id))}));
 return {decisive,risks,pending,observations,tasks,reviews};
}
function missingBasis(result,data=build(result)){
 const rows=[],used=new Set(),checks=result.applicable_checks||[],results=result.check_results||[];
 const add=(name,request,blocked,ruleId=null,materialIds=[])=>{if(!request||used.has(request))return;used.add(request);rows.push({name,request,blocked,ruleId,materialIds});};
 for(const check of checks){
  if(!check.required_now||check.applicability!=='applicable')continue;
  const outcome=results.find(item=>item.area_id===check.area_id&&item.key===check.key);
  if(!outcome?.customer_request||!['missing','unusable'].includes(outcome.status))continue;
  add(check.label,outcome.customer_request,`无法完成「${check.label}」核查：${check.evidence_requirement}。`,outcome.rule_ids?.[0]||null,outcome.material_ids||[]);
  if(check.key==='filing')used.add('补充电站备案证');
 }
 for(const row of result.evidence_coverage?.rows||[])for(const request of row.customer_requests||[])add(request,request,'无法完成「'+row.name+'」的关键部位核验；区域：'+row.area_id,'EVIDENCE-COVERAGE-MISSING-001',row.material_ids||[]);
 const decisionRequests=new Set(result.decision?.missing_requirements||[]);
 for(const task of data.tasks){
  if(!decisionRequests.has(task.text))continue;
  if(used.has(task.text))continue;
  const review=(result.material_reviews||[]).find(item=>(item.missing_requirements||[]).includes(task.text));
  const ruleId=review?.triggered_rule_ids?.find(id=>id==='IMG-WATERMARK-DATE-001')||review?.triggered_rule_ids?.[0]||
    (task.text==='补充项目准确经纬度'?'REQ-PROJECT-COORDINATES':task.text==='补充光伏组件完整型号'?'REQ-COMPONENT-MODEL':null);
  if(task.text==='补充清晰水印信息：拍摄日期')add('带清晰拍摄日期水印的现场照片',task.text,`无法核验现场照片的拍摄日期；缺少拟起保日期时，${watermarkDays(result)}日时效也无法完成核验。`,ruleId,task.materials);
  else if(task.text==='补充项目准确经纬度')add('项目准确经纬度',task.text,'无法定位项目并查询所在地的历史气象指标。',ruleId,task.materials);
  else if(task.text==='补充光伏组件完整型号')add('光伏组件完整型号',task.text,'无法匹配组件抗灾参数并完成相应灾害能力比较。',ruleId,task.materials);
  else add(task.text,task.text,'报告尚未写明这份材料影响哪项检查，需核保员确认。',ruleId,task.materials);
 }
 return rows;
}
function findingRule(item){
 const ids=item.rules||[];
 if(environment.has(item.category)){
  const preferred=item.environment_relation==='distant_background'?'ENV-BACKGROUND-CONTEXT-001':item.environment_relation==='operational_surroundings'?'ENV-SITE-RELATION-REVIEW-001':null;
  return (preferred&&ids.includes(preferred)?preferred:null)||ids.find(id=>id==='ENV-SITE-RELATION-REVIEW-001'||id==='ENV-BACKGROUND-CONTEXT-001'||id==='ENV-EXCLUDED-001')||ids.find(id=>id.startsWith('ENV-'))||null;
 }
 return ids.find(id=>id==='IMG-FINDING-WARNING-001')||ids.find(id=>!id.startsWith('IMG-WATERMARK-'))||null;
}
function warningRule(text,result){
 if(text.startsWith('拟起保日期未提供'))return 'IMG-WATERMARK-DATE-001';
 if(text.startsWith('部分自动识别服务未完整执行'))return 'SYS-PROVIDER-FAILURE';
 for(const review of result.material_reviews||[])if((review.reasons||[]).includes(text)){
  const ids=review.triggered_rule_ids||[];
  if(text.startsWith('交叉核对范围提示：')&&ids.includes('DOC-COMPARISON-FIELD-UNAVAILABLE-001'))return 'DOC-COMPARISON-FIELD-UNAVAILABLE-001';
  if(text.includes('水印')&&ids.includes('IMG-WATERMARK-001'))return 'IMG-WATERMARK-001';
  if(text.includes('远处背景')&&ids.includes('ENV-BACKGROUND-CONTEXT-001'))return 'ENV-BACKGROUND-CONTEXT-001';
  if(text.includes('邻近周边')&&ids.includes('ENV-SITE-RELATION-REVIEW-001'))return 'ENV-SITE-RELATION-REVIEW-001';
  return findingRule({category:text.includes('环境')?'agriculture_environment':'other',rules:ids})||(ids.length===1?ids[0]:null);
 }
 for(const stage of result.stage_reviews||[])if((stage.pending_questions||[]).includes(text))return stage.rule_ids?.[0]||null;
 return null;
}
const filingFieldNames={project_name:'项目名称',construction_content:'建设内容',project_entity:'项目单位',site_address:'建设地点'};
const installationNames={building_roof:'建筑屋顶式',carport_roof:'车棚顶式',ground_mount:'地面支架式',floating:'水上光伏',other_canopy:'其他棚架式',building_facade:'建筑立面式',other_known:'其他安装类型',unknown:'安装类型待核'};
function distinctLocationEvidence(location){
 const seen=new Set();return (location?.evidence||[]).filter(item=>{
  const key=item.longitude!=null&&item.latitude!=null
   ?`point:${item.longitude.toFixed(6)}:${item.latitude.toFixed(6)}:${item.coordinate_system||'未标明'}`
   :item.address?`address:${item.address.toLowerCase().replace(/\s+/g,'')}`:`material:${item.material_id||item.source}`;
  if(seen.has(key))return false;seen.add(key);return true;
 });
}
function locationComparisonText(location){
 const evidence=distinctLocationEvidence(location);
 const addresses=evidence.filter(item=>item.address&&['乡镇','街道','区县'].includes(item.geocode_level)&&item.geocode_candidates?.length===1);
 const photos=evidence.filter(item=>item.source==='现场照片水印'&&item.longitude!=null&&item.latitude!=null);
 if(addresses.length!==1||photos.length!==1)return null;
 const address=addresses[0],photo=photos[0],candidate=address.geocode_candidates[0];
 const left=candidate.latitude*Math.PI/180,right=photo.latitude*Math.PI/180;
 const dlat=right-left,dlon=(photo.longitude-candidate.longitude)*Math.PI/180;
 const distance=2*6371000*Math.asin(Math.sqrt(Math.sin(dlat/2)**2+Math.cos(left)*Math.cos(right)*Math.sin(dlon/2)**2));
 const unknown=!photo.coordinate_system||photo.coordinate_system==='未标明';
 return `备案地址“${address.address}”只定位到${address.geocode_level}级地图候选点，该候选点与现场照片水印坐标按原始数值估算相距约 ${Math.round(distance)} 米。这是区域线索，不是两处电站位置的准确距离；候选点并非电站实际位置${unknown?'，且照片坐标系未标明，不能直接套用 500 米或 2 公里阈值':''}。需核对项目级准确地址或坐标，以及照片是否属于该电站。`;
}
function decisiveEvidence(result,data=build(result)){
 const items=[];
 const add=item=>{if(item.materialId)items.push(item);};
 const location=result.location_assessment;
 if(result.decision?.decision==='recommend_reject'&&location?.address_photo_conflict&&(result.stage_reviews||[]).some(stage=>stage.stage==='location'&&stage.action==='recommend_reject')){
  const explanation='比较方法：先将项目地址经地图服务转换为候选经纬度，再与现场照片水印经纬度计算距离。'+((location.notes||[]).join('；')||'项目安装地址与现场坐标明显不对应');
  const locationEvidence=distinctLocationEvidence(location).sort((left,right)=>Number(!!right.address)-Number(!!left.address));
  for(const evidence of locationEvidence)if(evidence.material_id&&(evidence.address||evidence.longitude!=null&&evidence.latitude!=null)){
   add({kind:'location',materialId:evidence.material_id,title:evidence.source||'位置证据',
    explanation:[evidence.address,evidence.longitude!=null&&evidence.latitude!=null?`${evidence.longitude}, ${evidence.latitude}`:null,explanation].filter(Boolean).join(' · '),bbox:null});
  }
 }
 for(const stage of result.stage_reviews||[]){
  if(stage.action!=='recommend_reject')continue;
  if(stage.stage==='filing'){
   const fields=(result.ocr_fields||[]).filter(field=>(stage.ocr_field_ids||[]).includes(field.field_id)&&((stage.rule_ids||[]).includes('DOC-FILING-NAME-CONFLICT-001')||field.filing_assessment?.status==='confirmed'));
   for(const field of fields)add({kind:'filing',materialId:field.material_id,title:filingFieldNames[field.field_name]||businessText(field.field_name),explanation:field.filing_assessment?.reason||stage.reasons?.[0]||'请核对备案原文',quote:field.raw_value||field.normalized_value||field.evidence_text,bbox:field.filing_assessment?.evidence_bbox||null,page:field.page_number,fragment:field.filing_assessment?.evidence_quote||null,ruleId:stage.rule_ids?.[0]||null,refId:'ocr:'+field.field_id});
  }else if(['installation_type','installation_scope'].includes(stage.stage)){
   const cited=new Set(stage.material_ids||[]),regions=[];
   for(const area of result.installation_analysis?.areas||[])if(area.status==='confirmed')for(const region of area.evidence_regions||[])if(!cited.size||cited.has(region.material_id))regions.push({area,region});
   for(const {area,region} of regions)add({kind:'installation',materialId:region.material_id,title:installationNames[area.scene_type]||'安装类型',explanation:region.description,quote:null,bbox:region.bbox||null,ruleId:stage.rule_ids?.[0]||null});
  }
 }
 for(const finding of data.decisive)add({kind:'single',materialId:finding.material_id,title:finding.label||'单图拒保证据',category:finding.category,explanation:finding.evidence_text||'请核对原图',quote:null,bbox:finding.bbox||null,ruleId:findingRule(finding),refId:'finding:'+finding.finding_id});
 return items;
}
function groupBy(items,keyFor){
 const groups=[],byKey=new Map();
 for(const item of items){const key=keyFor(item);let group=byKey.get(key);if(!group){group={key,title:item.title||item.label||item.category||'风险提示',items:[]};byKey.set(key,group);groups.push(group);}group.items.push(item);}
 return groups;
}
const decisiveGroups=items=>groupBy(items,item=>`${item.kind}:${item.kind==='single'?item.category:item.title}`);
const riskGroups=items=>groupBy(items,item=>item.category||item.label);
function multiviewShadingFindings(result){
 if(result.decision?.decision!=='manual_review'||!(result.decision?.decisive_rule_ids||[]).includes('IMG-SHADING-MULTIVIEW-REVIEW-001'))return [];
 const panoramas=new Map((result.materials||[]).filter(m=>m.category==='panorama').map(m=>[m.material_id,m]));
 const found=(result.findings||[]).filter(f=>f.category==='minor_shading'&&f.detection_status==='detected'&&panoramas.has(f.material_id));
 const views=new Set(found.map(f=>panoramas.get(f.material_id).capture_view));
 return views.has('front_level')&&views.has('overhead')?found.filter(f=>['front_level','overhead'].includes(panoramas.get(f.material_id).capture_view)):[];
}
function warningCoveredByDisplayedFinding(warning,result,displayedIds){
 if(missingStartDateWarning(warning))return false;
 const findings=new Map((result.findings||[]).map(item=>[item.finding_id,item]));
 const matches=id=>displayedIds.has(id)&&findingMatchesWarning(findings.get(id)||{},warning);
 return (result.material_reviews||[]).some(review=>(review.reasons||[]).includes(warning)&&(review.finding_ids||[]).some(id=>{
  const finding=findings.get(id);
  return finding&&finding.material_id===review.material_id&&matches(id);
 }))||(result.stage_reviews||[]).some(stage=>(stage.pending_questions||[]).includes(warning)&&(stage.finding_ids||[]).some(matches));
}
function groupedWarningEntries(warnings,result,displayedIds){
 const entries=[];
 for(const raw of [...new Set(warnings||[])]){
  if(warningCoveredByDisplayedFinding(raw,result,displayedIds))continue;
  const label=warningText(raw);
  const location=/地址|坐标|经纬度|地图|位置/.test(raw)&&!missingStartDateWarning(raw);
  const key=location?'location:project':label;
  let existing=entries.find(item=>item.key===key);
  if(!existing){existing={key,label:location?'项目位置核对':label,raw:[],details:[]};entries.push(existing);}
  existing.raw.push(raw);
  if(location&&!existing.details.includes(label))existing.details.push(label);
 }
 return entries;
}
const missingStartDateWarning=warning=>/拟起保日期/.test(warning)&&/未提供|未填写|缺少|为空/.test(warning);
function captureDateEvidence(result,warning){
 const reviews=(result.material_reviews||[]).filter(review=>(review.triggered_rule_ids||[]).includes('IMG-WATERMARK-DATE-001')&&(!warning||(review.reasons||[]).includes(warning)));
 const materialIds=new Set(reviews.map(review=>review.material_id));
 return (result.ocr_fields||[])
  .filter(field=>['watermark_date','shot_date'].includes(field.field_name)&&field.value_status==='extracted'&&paddedEvidenceBox(field.bbox)&&(!materialIds.size||materialIds.has(field.material_id)))
  .map(field=>({kind:'date',materialId:field.material_id,title:'照片拍摄日期',explanation:`照片水印显示拍摄日期 ${field.normalized_value||field.raw_value}；拟起保日期未提供，暂无法核验 ${watermarkDays(result)} 日时效。`,quote:field.raw_value||field.normalized_value,bbox:field.bbox,refId:'ocr:'+field.field_id}));
}
function warningEvidence(rawWarnings,result){
 const items=[],seen=new Set(),findings=new Map((result.findings||[]).map(f=>[f.finding_id,f]));
 const add=item=>{const key=`${item.materialId}:${item.title}:${item.explanation}`;if(item.materialId&&!seen.has(key)){seen.add(key);items.push(item);}};
 for(const warning of rawWarnings){
  // The box proves only the photographed date, not the absent proposed start date.
  if(missingStartDateWarning(warning)){
   for(const item of captureDateEvidence(result,warning))add(item);
   continue;
  }
  for(const review of result.material_reviews||[]){
   if(!(review.reasons||[]).includes(warning))continue;
   const linked=(review.finding_ids||[]).map(id=>findings.get(id)).filter(f=>f&&f.material_id===review.material_id&&f.detection_status!=='not_detected'&&findingMatchesWarning(f,warning));
   if(linked.length)for(const f of linked)add({kind:'review',materialId:f.material_id,title:f.label||'现场证据',explanation:f.evidence_text||warning,bbox:f.bbox||null});
   else add({kind:'review',materialId:review.material_id,title:'关联材料',explanation:warning,bbox:null});
  }
  for(const stage of result.stage_reviews||[]){
   if(!(stage.pending_questions||[]).includes(warning))continue;
   const linked=(stage.finding_ids||[]).map(id=>findings.get(id)).filter(f=>f?.material_id&&findingMatchesWarning(f,warning));
   if(linked.length)for(const f of linked)add({kind:'review',materialId:f.material_id,title:f.label||'现场待核证据',explanation:f.evidence_text||warning,bbox:f.bbox||null});
   // Stage material_ids can contain the entire intake. They do not establish
   // which photograph proves this particular warning.
  }
  const location=result.location_assessment;
  if(location&&[...(location.notes||[]),...(location.missing_requirements||[])].includes(warning)){
   for(const evidence of distinctLocationEvidence(location))if(evidence.material_id)add({kind:'review',materialId:evidence.material_id,title:evidence.source||'位置证据',explanation:[evidence.address,evidence.longitude!=null&&evidence.latitude!=null?`${evidence.longitude}, ${evidence.latitude}`:null,warning].filter(Boolean).join(' · '),bbox:null});
  }
 }
 return items;
}
function findingMatchesWarning(finding,warning){
 const label=String(finding.label||'');
 if(!label)return false;
 // The finding's free-form explanation can mention an unrelated issue on the
 // same photo. Match only its short topic label to this warning.
 const generic=new Set(['现场','照片','材料','风险','复核','核对','检查','确认','位置','模型','需要','当前']);
 const pairs=text=>new Set((text.match(/[\u4e00-\u9fff]+/g)||[]).flatMap(part=>Array.from({length:Math.max(0,part.length-1)},(_,index)=>part.slice(index,index+2))).filter(pair=>!generic.has(pair)));
 const terms=pairs(label),warningTerms=pairs(warning);
 return [...terms].some(term=>warningTerms.has(term));
}
function basisEvidence(item,result){
 if(missingStartDateWarning(item.text))return captureDateEvidence(result);
 if(item.ref==='shading:multiview')return multiviewShadingFindings(result).map(f=>({kind:'review',materialId:f.material_id,title:f.label||'组件局部遮挡',explanation:f.evidence_text||'请核对组件表面及树枝、树影的位置',bbox:f.bbox||null,refId:'finding:'+f.finding_id}));
 if(item.ref==='location')return distinctLocationEvidence(result.location_assessment)
  .filter(evidence=>evidence.material_id)
  .map(evidence=>({kind:'location',materialId:evidence.material_id,title:evidence.source||'位置证据',explanation:evidence.address?`备案地址：${evidence.address}；地图定位精度：${evidence.geocode_level||'未确认'}`:`现场照片水印坐标：${evidence.longitude}, ${evidence.latitude}；坐标系：${evidence.coordinate_system||'未标明'}`,bbox:null}));
 const materialId=item.ref?.startsWith('material:')?item.ref.slice('material:'.length):null;
 if(!materialId)return [];
 const decisive=decisiveEvidence(result).filter(evidence=>
  evidence.materialId===materialId&&businessText(evidence.explanation||evidence.title)===item.text);
 if(decisive.length)return decisive;
 const findings=(result.findings||[]).filter(f=>f.material_id===materialId);
 const linked=findings.filter(f=>(f.evidence_text&&item.text.includes(f.evidence_text))
  ||(f.label&&item.text.includes(f.label)));
 if(linked.length)return linked.map(f=>({kind:'review',materialId,title:f.label||'关联原图',explanation:f.evidence_text||item.text,bbox:f.bbox||null}));
 return [{kind:'review',materialId,title:'关联原图',explanation:item.text,bbox:null}];
}
function appendHighlighted(parent,text,fragment){
 const source=String(text||'');if(!fragment||!source.includes(fragment)){parent.textContent=source;return;}
 const start=source.indexOf(fragment);parent.append(document.createTextNode(source.slice(0,start)),n('mark','',fragment),document.createTextNode(source.slice(start+fragment.length)));
}
function renderDecisiveEvidence(parent,item,index,{name,resolveSource,showTitle=true}){
 const card=n('article','uw-decisive-item');
 if(item.refId)card.id=refDomId(item.refId);
 const copy=n('div','uw-decisive-copy');copy.append(n('div','uw-decisive-index',`证据 ${index+1} · ${item.kind==='filing'?'备案证':item.kind==='installation'?'安装类型':item.kind==='location'?'位置核验':item.kind==='date'?'拍摄日期':item.kind==='review'?'待核原件':'现场风险'}`));if(showTitle)copy.append(n('h4','',item.title));copy.append(n('p','uw-decisive-explanation',businessText(item.explanation||'请核对原件')));
 if(item.quote){const quote=n('p','uw-decisive-quote');quote.append(n('strong','','原文：'));appendHighlighted(quote,item.quote,item.fragment);copy.append(quote);}
 const visual=n('figure','uw-decisive-visual');visual.append(n('figcaption','',name(item.materialId)));
 const box=paddedEvidenceBox(item.bbox,item.kind==='installation'?.02:.006,item.kind==='installation'?.025:.008);
 const source=resolveSource(item.materialId);
 if(source?.url){
  if(source.pdf){const frame=n('iframe');frame.src=source.url+(item.page?'#page='+item.page:'');frame.title=name(item.materialId)+' 原件';visual.append(frame);}
  else{const wrap=n('div','uw-image'),img=n('img');img.src=source.url;img.alt=name(item.materialId)+' 对应原图';img.loading='lazy';wrap.append(img);if(box){const overlay=n('span','uw-box '+(item.kind==='filing'?'filing':item.kind==='review'?'pending':'reject'));overlay.style.cssText=`left:${box.x_min*100}%;top:${box.y_min*100}%;width:${(box.x_max-box.x_min)*100}%;height:${(box.y_max-box.y_min)*100}%`;wrap.append(overlay);}visual.append(wrap);}
  const link=n('a','','查看完整原件');link.href=source.url;link.target='_blank';link.rel='noopener';visual.append(link);
 }else visual.append(n('p','uw-empty','本机未保存可预览原件，请在材料页核对。'));
 if(source?.pdf)visual.append(n('p','uw-decisive-location','PDF 按对应页展示；本页暂不能可靠叠加定位框，请结合上方标亮原文核对。'));
 else if(box)visual.append(n('p','uw-decisive-location','定位框由模型估计，请按原图核对。'));
 else visual.append(n('p','uw-decisive-location','本轮未取得可靠定位框，按原图核对。'));
 card.append(copy,visual);parent.append(card);
}
function weatherQueryReasons(result){
 const trace=(result.processing_trace||[]).find(t=>t.step==='weather_lookup');
 if(trace?.detail||trace?.error_message)return [trace.detail||trace.error_message];
 const diagnostics=(result.location_assessment?.evidence||[]).filter(e=>e.geocode_reason).map(e=>`${e.file_name||e.source}：${e.geocode_reason}`);
 if(diagnostics.length)return diagnostics;
 if(result.location_assessment?.status==='conflict')return ['位置证据存在冲突；此记录未保存气象查询的具体处理原因。'];
 return ['此记录未保存具体查询原因。重新运行后可查看地址解析结果及气象查询状态。'];
}
function screeningData(result){
 const unified=result.pv_load_screening_assessment;
 if(unified?.model_version?.startsWith("city-wind-snow")){const rows=["wind","snow"].map(h=>({hazard:h,capacity:unified[h].capacity_pa,demand:unified[h].load_pa?.max,ratio:unified[h].comparison_ratio?.max,status:unified[h].load_pa?"calculated":"insufficient",risk:unified[h].risk_level,persisted:true}));return {assessment:unified,rows,calculated:rows.filter(r=>r.status==="calculated").length,total:2,legacy:false};}
 const a=result.catastrophe_assessment,c=result.component_profile,w=result.weather_profile;
 const stored=a?.comparisons||[];
 const rows=['wind','snow'].map(hazard=>{
  const item=stored.find(r=>r.hazard===hazard);if(item)return {...item,persisted:true};
  const capacityKey={wind:'wind_load_pa',hail:'hail_resistance_mm',snow:'snow_load_pa'}[hazard];
  const eventKey={wind:'historical_max_wind_m_s',hail:'historical_max_hail_mm',snow:'historical_max_snow_load_pa'}[hazard];
  return {hazard,capacity:c?.[capacityKey]??null,event:w?.[eventKey]??null,demand:null,ratio:null,unit:hazard==='hail'?'mm':'Pa',status:a?'not_saved':'not_evaluated',risk:'unknown',persisted:false};
 });
 return {assessment:a,rows,calculated:rows.filter(r=>r.persisted&&r.status==='calculated').length,legacy:!!a&&!stored.length};
}
function buildReportView(result,data=build(result)){
 const decision=result.decision?.decision||null,checks=result.applicable_checks||[],outcomes=new Map((result.check_results||[]).map(item=>[item.area_id+':'+item.key,item]));
 const priced=priceApplies(result.decision);
 const findings=new Map((result.findings||[]).map(item=>[item.finding_id,item]));
 const multiviewShade=multiviewShadingFindings(result),shadeMaterialIds=[...new Set(multiviewShade.map(f=>f.material_id))];
 const coverage=checks.map(check=>{
  const outcome=outcomes.get(check.area_id+':'+check.key),linked=(outcome?.finding_ids||[]).map(id=>findings.get(id)).filter(Boolean);
  const linkedOcr=new Set(outcome?.ocr_field_ids||[]);
  const relevant=checkRiskCategories[check.key];
  const risk=linked.some(f=>f.detection_status==='detected'&&f.category!=='image_quality'&&(!relevant||relevant.has(f.category)))||(result.material_reviews||[]).some(r=>['surcharge','recommend_reject','conditional'].includes(r.action)&&(r.ocr_field_ids||[]).some(id=>linkedOcr.has(id)));
  const uncertain=linked.some(f=>f.detection_status==='uncertain'&&(!relevant||relevant.has(f.category)));
  const coverageRows=(result.evidence_coverage?.rows||[]).filter(r=>r.area_id===check.area_id&&(result.ruleset_snapshot?.rules||[]).some(rule=>rule.id===r.rule_id&&(rule.checks||[]).includes(check.key)));
  const coveragePending=coverageRows.some(r=>['insufficient','review'].includes(r.status));
  const state=check.applicability==='not_applicable'||outcome?.status==='not_applicable'?'not_applicable':!outcome||['missing','not_provided'].includes(outcome.status)?'not_executed':risk?'risk':coveragePending?'undetermined':outcome.status==='valid'&&!uncertain?'evidence_ready':'undetermined';
  const materialIds=outcome?.material_ids||[];
  const gapNotice=(result.material_reviews||[]).filter(review=>materialIds.includes(review.material_id)).flatMap(review=>review.reasons||[]).find(text=>/未返回可靠识别结果|部分字段未可靠识别/.test(text));
  return {id:'check:'+check.area_id+':'+check.key,label:check.label,key:check.key,state,scope:businessText(check.evidence_requirement),reason:businessText(outcome?.reason||check.reason),gapNotice:businessText(gapNotice),materialIds,findingIds:outcome?.finding_ids||[],ocrFieldIds:outcome?.ocr_field_ids||[],required:!!check.required_now,rawStatus:outcome?.status||null};
 });
 const counts=Object.fromEntries(['evidence_ready','risk','undetermined','not_executed','not_applicable'].map(state=>[state,coverage.filter(item=>item.state===state).length]));
 const actions=[],seen=new Set();
 const add=(owner,raw,blocked,after,refs=[],priority=2)=>{const text=businessText(raw);if(!text||seen.has(owner+':'+text))return;if(owner==='customer'&&text==='补充电站备案证'&&actions.some(item=>item.owner==='customer'&&item.text.includes('备案')))return;seen.add(owner+':'+text);actions.push({id:'action:'+(actions.length+1),owner,text,blocked:businessText(blocked),after:businessText(after),refs,priority});};
 for(const item of missingBasis(result,data)){
  if((result.evidence_coverage?.customer_requests||[]).includes(item.request))add('customer',item.request,item.blocked,'补充该部位证据后发起新轮核保',item.materialIds||[],1);
  else if(/加费方案|费率|承保条件/.test(item.request))add('business','确认业务规则：'+item.request,item.blocked,'明确方案后重新核定整单建议',item.materialIds||[],1);
  else if(/^(补充|补拍|重拍)/.test(item.request)&&!/系统|查询失败|规则待|接口失败/.test(item.request))add('customer',item.request,item.blocked,'补齐后重查对应材料与规则',item.materialIds||[],1);
  else add('underwriter','确认缺口责任归属：'+item.request,item.blocked,'明确处理人后重查',item.materialIds||[],2);
 }
 const known=new Set(missingBasis(result,data).map(item=>item.request));
 for(const request of result.decision?.missing_requirements||[]){if(known.has(request))continue;if(/加费方案|费率|承保条件/.test(request))add('business','确认业务规则：'+request,'业务规则尚未确定','明确方案后重新核定整单建议',[],1);else if(/^(补充|补拍|重拍)/.test(request)&&!/系统|查询失败|规则待|接口失败/.test(request))add('customer',request,'综合判断受阻','补齐后重查受影响检查',[],1);else add('underwriter','确认缺口责任归属：'+request,'综合判断受阻','明确处理人后重查');}
 if(result.location_assessment?.status==='conflict'){
  if((result.stage_reviews||[]).some(stage=>stage.stage==='location'&&stage.action==='recommend_reject'))add('underwriter','核对拒保位置证据并记录最终结论','位置冲突已触发建议拒保','记录人工审核结论',[],1);
  else add('underwriter','核对坐标系、位置原件与项目现场关系','重大位置冲突，已暂停自动核保','确认位置后重查气象数据与整单建议',[],1);
 }
 else if(['single_source','uncertain','missing'].includes(result.location_assessment?.status))add('underwriter','核对备案地址与现场照片位置是否属于同一项目','位置尚未交叉核验一致','确认位置后重查气象数据与整单建议',(result.location_assessment?.evidence||[]).filter(item=>item.material_id).map(item=>item.material_id),1);
 for(const trace of result.processing_trace||[])if(['failed','partial'].includes(trace.status))add('system','重新运行'+(processingStepNames[trace.step]||'未完成的自动检查'),'相关检查尚未完成','完成后重新查看受影响的检查与建议',[],1);
 const ocrPending=[...(result.decision?.reasons||[]),...(result.material_reviews||[]).flatMap(review=>review.reasons||[]),...(result.check_results||[]).map(check=>check.reason||'')].filter(reason=>/以下字段未返回可靠识别结果|自动识别失败或未覆盖检查事项/.test(reason));
 if(ocrPending.length){const fields=[...new Set(ocrPending.flatMap(reason=>(String(reason).match(/\b[a-z][a-z0-9_]*\b/g)||[]).filter(key=>fieldNames[key]).map(key=>fieldNames[key])))];add('system','文字识别待重试：'+(fields.join('、')||'未识别的材料文字'),'相关材料文字尚无法可靠核验','重查受影响检查；仍无法识别时核对原件',[],1);}
 if(multiviewShade.length)add('underwriter','核对正面与俯拍遮挡标框，确认实际受影响组件及范围','两张全景图均标记组件局部遮挡','核对原图及现场情况，记录人工复核结论',shadeMaterialIds,0);
 if((result.decision?.warnings||[]).some(missingStartDateWarning))add('underwriter',`确认拟起保日期并核验照片拍摄时间是否符合 ${watermarkDays(result)} 日要求`,'当前缺少拟起保日期，时效暂无法核验','取得日期后完成时效核验',[],1);
 for(const check of checks)if(check.applicability==='pending'&&/尚未核实是否配置/.test(check.reason||''))add('underwriter','确认项目是否配置'+check.label,check.label,'明确是否适用后重查该项',[],1);
 if(decision==='recommend_reject')add('underwriter','核对拒保原件、现场关系与适用规则','最终拒保确认','记录人工审核结论',[],1);
 else if(decision==='manual_review'&&result.location_assessment?.status!=='conflict'&&!actions.length)add('underwriter','核实关键冲突或不确定事实','自动判断已暂停','核实后重跑或记录人工结论',[],1);
 else if(decision==='conditional_accept')add('underwriter','确认承保条件的条款和满足情况','条件尚未人工确认','记录最终承保条件',[],1);
 else if(decision==='accept')add('underwriter','复核关键证据并确认承保','系统建议待确认','记录最终核保结论',[],1);
 if(priced)add('business','确认正式加费依据与适用方案','加费建议待批准','由核保员确认最终条件',[],1);
 const ownerFirst=({recommend_reject:'underwriter',manual_review:'underwriter',request_more:'customer',surcharge:'business',conditional_accept:'underwriter',accept:'underwriter'})[decision];
 actions.sort((a,b)=>(a.owner===ownerFirst?0:1)-(b.owner===ownerFirst?0:1)||a.priority-b.priority);
 const basis=[],basisTexts=new Set();
 const push=(raw,impact,ref=null)=>{const text=businessText(raw);if(!text||/^(部分检查项仍待内部复核|需人工核实关键问题)$/.test(text))return;
  if(/以下字段未返回可靠识别结果|自动识别失败或未覆盖检查事项|可选材料部分字段未可靠识别/.test(raw)){
   const named=(String(raw).match(/\b[a-z][a-z0-9_]*\b/g)||[]).filter(key=>fieldNames[key]).map(key=>fieldNames[key]);
   const fields=named.length?named:(text.split('：')[1]||'部分材料文字').split(/[，,、]/).map(part=>part.trim()).filter(Boolean);
   let item=basis.find(row=>row.kind==='ocr_gap');if(!item){item={kind:'ocr_gap',fields:[],text:'',impact:'系统重试；仍无法识别时由核保员核对原件',ref};basis.push(item);}for(const field of fields)if(!item.fields.includes(field))item.fields.push(field);item.text='文字识别待核：'+item.fields.join('、');if(!item.ref&&ref)item.ref=ref;return;
  }
  if(basisTexts.has(text))return;basisTexts.add(text);basis.push({text,impact,ref});};
 if(decision==='recommend_reject')for(const item of decisiveEvidence(result,data))push(item.explanation||item.title,'决定本轮拒保建议',item.materialId?'material:'+item.materialId:null);
 if(decision==='request_more')for(const item of actions.filter(action=>action.owner==='customer'))push('缺少：'+item.text,'阻断'+item.blocked,null);
 if(decision==='manual_review'){
  if(['conflict','uncertain','single_source'].includes(result.location_assessment?.status)){
   const location=result.location_assessment;
   push(locationComparisonText(location)||location.notes?.[0]||location.missing_requirements?.[0]||'项目位置尚未交叉核验一致','需核对备案与现场位置原件','location');
  }
  if(multiviewShade.length)push('正面与俯拍照片均标记组件局部遮挡；遮挡范围及实际影响需人工核实',multiviewShade.every(f=>paddedEvidenceBox(f.bbox))?'两张原图分别有定位框':'部分原图未取得可靠定位框，需按原图核对','shading:multiview');
  for(const stage of result.stage_reviews||[])if(stage.action==='manual_review')for(const text of [...(stage.reasons||[]),...(stage.pending_questions||[])]){
   if(missingStartDateWarning(text))continue;
   if(multiviewShade.length&&stage.stage==='single_image'&&(stage.material_ids||[]).some(id=>shadeMaterialIds.includes(id))&&text.includes('遮挡'))continue;
   push(text,'需人工核实',stage.material_ids?.[0]?'material:'+stage.material_ids[0]:null);
  }
  const decisiveRules=result.decision?.decisive_rule_ids||[];
  if(decisiveRules.includes('CAT-FRONT-LOAD-SCREENING-REVIEW'))push('风雪风险量化：'+(result.pv_load_screening_assessment?.label||'结果待核'),'风雪专项结果需复核');
  if(decisiveRules.includes('SYS-PROVIDER-FAILURE')){
   const failed=[...new Set((result.processing_trace||[]).filter(trace=>['failed','partial'].includes(trace.status)).map(trace=>processingStepNames[trace.step]||trace.step||'自动检查'))];
   push('自动检查未完整执行：'+(failed.join('、')||'待核查步骤'),'需重试未完成的检查');
  }
 }
 if(['surcharge','conditional_accept'].includes(decision)||priced)for(const review of result.material_reviews||[])if((decision==='conditional_accept'&&review.action==='conditional')||(priced&&review.action==='surcharge'))for(const text of review.reasons||[])push(text,'决定本轮系统建议','material:'+review.material_id);
 if(decision==='accept')push('本轮 '+counts.evidence_ready+' 项有可核材料；系统未在已查范围识别到对应风险','支持系统建议；仍须核对已查范围和未知项');
 if(!basis.length)for(const text of (result.decision?.reasons||[]).filter(text=>decision!=='manual_review'||!text.startsWith('拟起保日期未提供')).slice(0,3))push(text,'需核对原始依据');
 const [workflowAction,progression]=({recommend_reject:['待确认','可进入拒保依据复核'],request_more:['等待阻断事项处理','需先处理阻断项'],manual_review:['待复核','暂停自动判断'],surcharge:['等待加费方案确认','可进入加费依据复核'],conditional_accept:['等待承保条件确认','可进入条件复核'],accept:['待确认','可进入承保复核']})[decision]||['等待系统生成建议','尚不能推进'];
 return {basis,actions,coverage,counts,workflowAction,progression};
}
function renderInterpretation(parent,result,{reviewRevision=''}){
 const box=n('section','uw-ai-summary');
 const toolbar=n('div','uw-ai-toolbar');
 const button=n('button','uw-ai-button','生成 AI 总结');button.type='button';button.setAttribute?.('aria-expanded','false');
 const status=n('span','uw-ai-status','按需生成本轮报告摘要');toolbar.append(button,status);
 const panel=n('div','uw-ai-panel');panel.hidden=true;
 const header=n('div','uw-ai-panel-header');header.append(n('strong','','AI 总结'));
 const refresh=n('button','uw-ai-refresh','↻');refresh.type='button';refresh.hidden=true;refresh.title='重新生成总结';refresh.setAttribute?.('aria-label','重新生成总结');header.append(refresh);
 const output=n('div','uw-ai-output');const draft=n('div','uw-ai-draft');draft.hidden=true;
 panel.append(header,output,draft);box.append(toolbar,panel);parent.append(box);
 let lastSummary='',started=false,inFlight=false;
 const generate=async(forceRefresh=false)=>{
  if(inFlight)return;
  started=true;inFlight=true;refresh.disabled=true;
  if(!forceRefresh)output.textContent='';
  draft.textContent='';draft.hidden=true;
  status.textContent=forceRefresh?'正在重新生成摘要，完成前保留上一版。':'内容生成中，请等待完成后核对原件。';
  try{
   const response=await fetch('/api/v1/reports/interpret/stream',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({case:result,review_revision:reviewRevision,force_refresh:forceRefresh})});
   if(!response.ok){const error=await response.json().catch(()=>({}));throw Error(error.detail||'AI 摘要暂不可用');}
   if(!response.body?.getReader)throw Error('当前浏览器无法读取流式结果');
   const reader=response.body.getReader(),decoder=new TextDecoder();let pending='',summary='',completed=false;
   const consume=line=>{if(!line.trim())return;const event=JSON.parse(line);if(event.type==='token'){summary+=event.text||'';if(forceRefresh){draft.hidden=false;draft.textContent=businessText(summary);}else output.textContent=businessText(summary);}else if(event.type==='done')completed=true;else if(event.type==='error')throw Error(event.message||'AI 摘要生成中断');};
   while(true){const {value,done}=await reader.read();if(done)break;pending+=decoder.decode(value,{stream:true});let newline;while((newline=pending.indexOf('\n'))>=0){consume(pending.slice(0,newline));pending=pending.slice(newline+1);}}
   pending+=decoder.decode();if(pending.trim())consume(pending);
   if(!completed||!summary.trim())throw Error('AI 摘要未完整生成，请重试');
   lastSummary=businessText(summary.trim());output.textContent=lastSummary;
   status.textContent=(forceRefresh?'已重新生成。':'')+'摘要仅供参考，最终结论以核保审核为准。';
  }catch(error){output.textContent=lastSummary;status.textContent=error?.message||'AI 摘要暂不可用，请查看原报告。';}
  finally{draft.textContent='';draft.hidden=true;inFlight=false;refresh.disabled=false;refresh.hidden=false;}
 };
 button.addEventListener('click',()=>{
  panel.hidden=!panel.hidden;
  button.textContent=panel.hidden?'展开总结':'收起总结';
  button.setAttribute?.('aria-expanded',String(!panel.hidden));
  if(!panel.hidden&&!started)return generate();
 });
 refresh.addEventListener('click',()=>generate(true));
}
function renderComponentModule(report,result){
 const a=result.catastrophe_assessment||{},r=a.module_result||result.component_module_result||result.component_profile?.workbench_result||{},p=r.profile||{};
 const section=n('section','uw-section uw-screening');section.id='uw-component-evidence';
 section.append(n('h3','','组件型号与抗灾参数'),n('p','',a.explanation||'组件来源参数核验；风雪模块执行情况另行记录。'));
 section.append(n('p','uw-meta','型号：'+(p.component_model||result.project?.component_model||'待识别')+' · 厂商：'+(p.manufacturer||'未取得')+' · 查询状态：'+({case_confirmed:'已采用本案人工确认参数',verified_reuse:'已复用人工核验来源参数',parameters_mapped:'查询成功，已取得正反面机械载荷',parameters_found:'查询成功，已取得独立风雪参数',partial:'查询完成，参数尚不完整',conflict:'查询完成，参数存在冲突',source_unavailable:'查询未成功，来源暂不可用'}[r.status]||'待核验')));
 if(r.status==='parameters_mapped')section.append(n('p','uw-meta','本次未取得厂家单独列示的风荷载、雪荷载字段。下表的风雪数值是正反面机械载荷的方向映射参考，不是独立的设计荷载参数。'));
 const table=n('table'),head=n('tr');['参数','数值','来源口径','状态'].forEach(x=>head.append(n('th','',x)));table.append(head);
 const states={found:'已取得',mechanical_mapped:'机械载荷映射',conflict:'冲突待核验',unknown:'未取得',observed_not_found:'资料未披露',identity_unconfirmed:'型号待确认'};
 for(const item of [...Object.entries(p.ui_fields||{}).filter(([key])=>!key.startsWith('hail_')).map(([,value])=>value),...Object.values(p.mechanical_fields||{})]){
  const tr=n('tr');[(item.value_origin==='mechanical_mapping'?item.label+'（机械映射参考）':item.label),item.value==null?'未取得':item.value+' '+item.unit,item.mapping_note||({test:'测试载荷',design:'设计载荷',unspecified:'测试／设计口径未明确'}[item.load_basis]||'口径未明确'),states[item.status]||item.status].forEach(x=>tr.append(n('td','',String(x||''))));table.append(tr);
 }section.append(table);
 for(const [field,records] of Object.entries(p.parameter_evidence||{})){
  if(field.startsWith('hail_'))continue;
  const details=n('details');details.append(n('summary','',({front_static_load_pa:'正面机械载荷',back_static_load_pa:'背面机械载荷',wind_load_pa:'风荷载',snow_load_pa:'雪荷载',rated_power_w:'额定功率'}[field]||'其他参数')+' · 来源证据'));
  for(const e of records){details.append(n('p','',String(e.quote||e.document_title||'原文未记录')),n('p','uw-meta','页码：'+(e.page??'未记录')+' · 口径：'+(e.load_basis||'未标注')));if(/^https?:\/\//.test(e.source_url||'')){const link=n('a','','查看原始来源');link.href=e.source_url;link.target='_blank';link.rel='noopener noreferrer';details.append(link);}}
  section.append(details);
 }
 const details=n('details');details.append(n('summary','','型号识别与完整查询过程'),n('pre','',JSON.stringify(r,null,2)));section.append(details);report.append(section);
}
function renderUnifiedScreening(report,a,result={}){
 const box=n('section','uw-section uw-screening uw-risk-screen');box.id='uw-screening';box.append(n('h3','','风雪风险量化'));
 const rows=['wind','snow'].map(h=>({h,row:a[h]||{}}));
 const valid=row=>Number.isFinite(row.load_pa?.max)&&Number.isFinite(row.capacity_pa)&&row.capacity_pa>0;
 const ratio=row=>row.load_pa.max/row.capacity_pa;
 const state=row=>!valid(row)?'unknown':ratio(row)>1?'danger':row.uncertainty_crosses_grade||['medium','high','critical'].includes(row.risk_level)?'attention':'clear';
 const states=rows.map(({row})=>state(row));
 const tone=states.includes('danger')?'danger':states.includes('unknown')?'unknown':states.includes('attention')?'attention':'clear';
 const titles={danger:'发现荷载超出提示',unknown:'暂无法完整判断',attention:'未超出，存在需关注事项',clear:'未发现超载提示'};
 const summary=n('div','uw-risk-verdict uw-risk-'+tone);summary.append(n('strong','','风雪初筛：'+titles[tone]));
 summary.append(n('p','',tone==='danger'?'至少一项测算荷载超过本轮对比标称载荷，请优先核查。':tone==='unknown'?'部分项目缺少可用数据，不能据此判断风险较低。':'已计算项目的荷载需求未超过本轮对比标称载荷；待核事项另列如下。'));box.append(summary);
 const grid=n('div','uw-hazard-grid');
 for(const {h,row}of rows){const st=state(row),card=n('article','uw-hazard-card uw-risk-'+st);card.append(n('h4','',(h==='wind'?'风':'雪')+'：'+({danger:'超出标称载荷',unknown:'暂无法判断',attention:'未超出 · 需关注',clear:'未超出'}[st])));
  if(valid(row)){card.append(n('p','uw-hazard-number',row.load_pa.max.toFixed(1)+' Pa'),n('p','uw-meta','本轮测算不利荷载'),n('p','',`占对比标称载荷的 ${(ratio(row)*100).toFixed(1)}% · 标称载荷 ${row.capacity_pa} Pa`));if(st==='danger')card.append(n('p','',`超过标称载荷 ${((ratio(row)-1)*100).toFixed(1)}%`));}
  else card.append(n('p','',row.reason||'缺少可用的荷载需求或组件参数，请核对资料。'));
  grid.append(card);
 }box.append(grid);
 const pending=[];
 for(const {h,row}of rows){const name=h==='wind'?'风':'雪',cap=a.component_capacities?.[h]||{};
  if(!valid(row))pending.push(name+'：'+(row.reason||'补齐该项计算资料后重新评估。'));
  else {const records=cap.evidence||[];if(!records.length||records.some(e=>!['test','design'].includes(e.load_basis)))pending.push('核实厂家载荷的测试／设计口径。');else if(records.some(e=>e.load_basis==='test'))pending.push('本轮使用厂家测试载荷，请核实其与现场安装条件的适用性。');
   if(row.uncertainty_crosses_grade)pending.push(name+'的参数区间跨越初筛等级，请核实安装参数。');
   if(ratio(row)>1)pending.push(name+'荷载超出本轮标称载荷，请核实组件能力及安装条件。');
  }
 }
 if(result.location_assessment&&!['verified','distance_screened'].includes(result.location_assessment.status))pending.push('项目位置尚未完成交叉核验，请核对备案地址与现场照片。');
 if(pending.length){const panel=n('div','uw-risk-pending');panel.append(n('strong','','待核实'));const list=n('ul');for(const item of [...new Set(pending)])list.append(n('li','',item));panel.append(list);box.append(panel);}
 const profile=result.component_module_result?.profile||result.component_profile;
 if(profile)box.append(n('p','uw-meta',`组件：${profile.manufacturer||'厂商待确认'} · ${profile.component_model||'型号待确认'}`));
 if(a.component_capacities?.wind?.field==='front_static_load_pa')box.append(n('p','uw-meta','本轮正面风压与正面机械载荷对比；背面机械载荷仅作风吸力参考，本轮未评价风吸力。'));
 box.append(n('p','uw-risk-scope','评估范围：10年重现期场景、固定支架、正面均匀受压；未评价背面风吸力。本结果不代表出险概率或自动承保结论。'));
 const loc=a.location_match||{};box.append(n('p','uw-meta',loc.status==='MATCHED'?`取值站点：${loc.province}·${loc.city_source}。占比为测算需求÷本轮对比标称载荷。`:loc.message||'取值站点待确认。'));
 const d=n('details','uw-risk-evidence');d.append(n('summary','','查看计算依据与厂家资料'));
 for(const[h,name]of[['wind','风'],['snow','雪']]){const row=a[h]||{},cap=a.component_capacities?.[h]||{};if(row.reason)d.append(n('p','',name+'：'+row.reason));if(row.trace?.evaluated_combinations){const w=row.trace.evaluated_combinations.reduce((x,y)=>x.load_pa>y.load_pa?x:y);d.append(n('p','',`风：${row.trace.q_pa} Pa × βgz ${w.beta_gz} × μz ${w.mu_z} × μsw ${w.mu_sw.toFixed(3)} = ${w.load_pa.toFixed(1)} Pa。`));}else if(row.trace)d.append(n('p','','雪：基本雪压 × 1000 × 均匀积雪系数1.0。'));if(cap.source)d.append(n('p','uw-meta',name+'对比字段：'+cap.field+'；'+cap.source));}
 for(const t of [...new Set(a.actions||[])])d.append(n('p','',t));for(const t of a.limitations||[])d.append(n('p','uw-meta',t));if(result.component_module_result?.profile)renderComponentModule(d,{...result,catastrophe_assessment:{module_result:result.component_module_result,explanation:'厂家资料与型号识别依据'}});box.append(d);report.append(box);
}
function renderScreening(report,result){
 if(result.pv_load_screening_assessment?.model_version?.startsWith("city-wind-snow")){renderUnifiedScreening(report,result.pv_load_screening_assessment,result);return;}
 if(result.catastrophe_assessment?.module==='component-workbench-v1'){renderComponentModule(report,result);return;}
 if(result.pre_catastrophe_outcome&&result.pre_catastrophe_outcome!=='continue'){
  const paused=n('section','uw-section uw-screening');paused.id='uw-screening';paused.append(n('h3','','风、雪检查'),n('p','uw-empty','前置必查事项尚未通过，本轮未执行风、雪筛查。'));report.append(paused);return;
 }
 const {assessment:a,rows,calculated,legacy}=screeningData(result),c=result.component_profile,w=result.weather_profile;
 const hazardNames={wind:'风灾',hail:'雹灾',snow:'雪灾'};
 const section=n('section','uw-section uw-screening');section.id='uw-screening';section.append(n('h3','','风、雪检查'));report.append(section);
 const compact=calculated===0||['request_more','manual_review'].includes(result.decision?.decision);
 const content=compact?n('details','uw-screening-compact'):section;
 if(compact){content.append(n('summary','',calculated?'已有初步数据，展开核对风雪两项灾害':'现有数据无法比较，展开查看缺口'));section.append(content);}
 const summary=n('div','uw-screening-summary');summary.append(n('strong','',!a?'本次尚未检查风、雪':calculated<2?'只有部分灾害能做初步比较':'风雪两项灾害已有初步比较'));
 summary.append(n('p','',a?.explanation||'本次记录没有抗灾评估结果，不能据此认定项目风险低。'));
 if(a)summary.append(n('div','uw-meta',`风、雪中有 ${calculated} 项算出了初步比较值；${a.requires_manual_review?'仍需人工核对。':'请按已取得的数据和检查范围复核。'}这些数值不能直接决定是否承保。`));
 if(legacy)summary.append(n('p','uw-meta','历史记录未保存逐项计算明细；以下保留原始输入与既有结论，不重新计算。重新运行整条链路可生成完整明细。'));
 const windTrace=(result.processing_trace||[]).find(t=>t.step==='catastrophe_assessment');if(windTrace)summary.append(n('div','uw-meta','自动检查：'+({success:'已运行',partial:'部分完成',failed:'运行失败'}[windTrace.status]||'状态待核对')));
 const date=value=>{if(!value)return '未记录';const parsed=new Date(value);return Number.isNaN(parsed.getTime())?String(value):parsed.toLocaleDateString('zh-CN');};
 summary.append(n('p','uw-screening-source',`资料来源：组件参数${c?.source_name?'来自 '+c.source_name:'未取得'}（取得于 ${date(c?.retrieved_at)}）；当地历史天气${w?.source_name?'来自 '+w.source_name:'未取得'}（取得于 ${date(w?.retrieved_at)}）。`));
 summary.append(n('p','uw-screening-source',result.location_assessment?.weather_coordinates_verified?'气象查询位置已核对；仍需确认组件参数与本项目安装条件相符。':result.location_assessment?.status==='distance_screened'?'位置距离已按 500 米口径初筛通过；气象查询坐标仍是候选参考，不视为项目级精确定位。':'气象查询位置尚未完成核对；请核保员先核实项目位置，再确认是否需要系统重新查询。'));
 if(!w){const query=n('div','notice'),weatherTrace=(result.processing_trace||[]).find(item=>item.step==='weather_lookup');query.append(n('strong','',weatherTrace?.status==='skipped'?'历史气象 · 跳过':weatherTrace?.status==='failed'?'历史气象 · 查询失败':'历史气象未取得'));for(const reason of weatherQueryReasons(result))query.append(n('p','',reason));for(const item of result.location_assessment?.evidence||[])for(const candidate of item.geocode_candidates||[])query.append(n('p','uw-meta',`候选地址：${candidate.address} · 定位级别 ${candidate.level}（尚未采用）`));summary.append(query);}
 content.append(summary);
 const f=value=>typeof value==='number'&&Number.isFinite(value)?value.toLocaleString('zh-CN',{maximumFractionDigits:3}):'—';
 const wrap=n('div','uw-screening-table');const table=n('table');const head=n('thead'),hr=n('tr');['灾害','组件厂家资料','当地历史天气','目前能否判断','还需核对什么'].forEach(text=>hr.append(n('th','',text)));head.append(hr);table.append(head);const body=n('tbody');
 for(const row of rows){const tr=n('tr');const status=row.status==='calculated'?'可做初步比较，尚不能下承保结论':row.status==='insufficient'?'无法判断':row.status==='not_saved'?'缺少计算记录':'未执行';
  const gaps=[];if(row.capacity==null)gaps.push('缺组件厂家数据');if(row.event==null)gaps.push('缺当地历史天气数据');if(row.status==='calculated')gaps.push('核对项目设计要求、安装方式与厂家试验条件');if(!gaps.length&&row.status==='insufficient')gaps.push(row.reason||'核对数据是否可比');if(row.status==='not_saved')gaps.push('查看原始报告或重新检查');if(row.status==='not_evaluated')gaps.push('确认该项为何尚未检查');
  const weatherValue=row.event==null?'未取得':f(row.event)+' '+(row.hazard==='wind'?'m/s':row.unit)+(row.hazard==='wind'&&row.demand!=null?`；按模型换算的风荷载 ${f(row.demand)} Pa`:'');
  [hazardNames[row.hazard],row.capacity==null?'未取得':f(row.capacity)+' '+row.unit,weatherValue,status,gaps.join('；')].forEach((text,index)=>tr.append(n(index===0?'th':'td',index===3?'uw-screening-state':'',text)));body.append(tr);
 }table.append(body);wrap.append(table);content.append(wrap,n('p','uw-meta','单位说明：Pa（帕）表示承受的压力，m/s（米/秒）表示风速；不同单位不能直接比较。'));
 const details=n('details');details.append(n('summary','','查看比较过程与数据缺口'));
 for(const row of rows){const item=n('div','uw-rule');item.append(n('strong','',hazardNames[row.hazard]));if(row.persisted){item.append(n('p','',row.formula));if(row.status==='calculated')item.append(n('p','',`${f(row.capacity)} ÷ ${f(row.demand)} = ${row.ratio==null?'未形成正值比较需求，不计算比值':f(row.ratio)}`));if(row.reason)item.append(n('p','',row.reason));}else item.append(n('p','',a?'未保存该项计算过程，请以已有结论和原始依据为准。':'该项未评估。'));details.append(item);}
 if(a?.critical_shortfall_ratio!=null&&a?.adequate_margin_ratio!=null)details.append(n('p','',`本次阈值：能力比＜${a.critical_shortfall_ratio} 明显不足；${a.critical_shortfall_ratio}～＜1 能力不足；1～＜${a.adequate_margin_ratio} 余量有限；≥${a.adequate_margin_ratio} 余量充足。`));else details.append(n('p','uw-meta','此记录未留存完整阈值，不使用当前阈值解释历史结果。'));
 for(const factor of a?.factors||[])details.append(n('p','',factor));content.append(details);
 const sources=n('details');sources.append(n('summary','','组件参数与历史气象来源'));
 function source(title,profile){const box=n('div','uw-rule');box.append(n('strong','',title));if(!profile){box.append(n('p','',title+'未取得；该部分不能完成筛查。'));sources.append(box);return box;}box.append(n('p','',profile.source_name||'未留存数据来源'));
  if(profile.source_url&&/^https?:\/\//.test(profile.source_url)){const link=n('a','','查看数据来源');link.href=profile.source_url;link.target='_blank';link.rel='noopener';box.append(link);}if(profile.retrieved_at)box.append(n('div','uw-meta','取得时间：'+new Date(profile.retrieved_at).toLocaleString('zh-CN')));sources.append(box);return box;}
 const component=source('组件参数',c);if(c){component.append(n('p','',`型号：${c.component_model}；厂商：${c.manufacturer||'未提供'}`));if(c.front_static_load_pa!=null||c.back_static_load_pa!=null)component.append(n('p','uw-meta',`静态载荷参考：正面 ${f(c.front_static_load_pa)} Pa，背面 ${f(c.back_static_load_pa)} Pa；未自动当作风、雪荷载能力。`));for(const note of c.lookup_notes||[])component.append(n('p','uw-meta',note));for(const [key,url] of Object.entries(c.parameter_sources||{})){if(!/^https?:\/\//.test(url))continue;const p=n('p','uw-meta'),link=n('a','',key+' · 参数来源');link.href=url;link.target='_blank';link.rel='noopener';p.append(link);component.append(p);}}
 const weather=source('历史气象',w);if(w){weather.append(n('p','',`查询坐标：${w.longitude}, ${w.latitude}；观测范围：${w.observation_start||'未记录'} 至 ${w.observation_end||'未记录'}`),n('p','uw-meta',result.location_assessment?.weather_coordinates_verified?'气象查询位置已核验。':result.location_assessment?.status==='distance_screened'?'位置距离初筛通过；气象数据仍按候选位置解读。':'气象数据按候选位置解读；位置尚未完成核验。'));if(w.historical_max_daily_snowfall_cm!=null)weather.append(n('p','uw-meta',`历史最大单日降雪 ${f(w.historical_max_daily_snowfall_cm)} cm，仅供气候背景参考，不自动换算为雪荷载。`));}
 content.append(sources,n('p','uw-meta','结果为组件抗灾能力与历史指标的筛查，不代表出险概率、预计赔款或整套电站承载合格；当前阈值仍需业务确认。'));
}
function renderReportProgress(report,result,missingCount=0){
 const progress=global.AnalysisProgress;
 if(!progress?.derive)return;
 const stages=progress.derive(result);
 const labels={done:'已完成',stopped:'提前停止',attention:'待处理',partial:'部分完成',failed:'执行失败',skipped:'未执行',unrecorded:'未记录',waiting:'待运行'};
 const section=n('section','uw-progress');section.append(n('h3','','审查进度'));
 const list=n('ol','uw-progress-list');
 stages.forEach((stage,index)=>{
  const staleCatastrophe=index===5&&result.decision?.decision==='request_more'&&result.catastrophe_assessment;
  const displayStatus=staleCatastrophe?'attention':stage.status;
  const item=n('li','uw-progress-step '+displayStatus);
  const hasRisk=index===3&&(result.findings||[]).some(finding=>finding.detection_status==='detected'&&!neutral.has(finding.category)&&finding.category!=='image_quality'&&finding.severity!=='info')||index===5&&(result.catastrophe_assessment?.comparisons||[]).some(item=>['high','critical'].includes(item.risk));
  const statusText=index===4&&result.decision?.decision==='request_more'?(missingCount?`缺 ${missingCount} 项材料`:'需补充材料'):
   index===5&&result.decision?.decision==='request_more'&&result.catastrophe_assessment?'需重查':
   hasRisk&&stage.status==='done'?'有风险提示':labels[stage.status]||'待核对';
  item.append(n('span','uw-progress-marker',displayStatus==='done'?'✓':displayStatus==='skipped'?'—':displayStatus==='attention'||displayStatus==='stopped'?'!':String(index+1)),n('strong','',stage.short),n('small','',statusText));
  if(hasRisk)item.className+=' has-risk';
  list.append(item);
 });
 section.append(list);report.append(section);
}
function renderRuleScope(report,result){
 const snapshot=result.ruleset_snapshot;if(!snapshot?.snapshot_id)return;
 const section=n('section','uw-section');section.append(n('h3','','本次核保范围与规则版本'));
 const selection=snapshot.selection||{};
 section.append(n('p','',`${snapshot.scheme_name} · 方案版本 ${selection.scheme_version||'自定义'}${snapshot.modified_from_scheme?'（本次已调整，以快照为准）':''} · 规则目录 ${snapshot.catalog_version}`));
 section.append(n('p','uw-meta','仅对已执行的所选规则提供结论；未选择、场景不适用和材料不足分别记录。'));
 const statuses={not_selected:'未选择',not_applicable:'场景不适用',applicability_pending:'适用条件待确认',dependency_pending:'前置规则待确认',not_executed:'未执行',material_missing:'材料不足',evidence_insufficient:'证据待核',risk_found:'发现关注事项',completed:'检查已完成'};
 const table=n('table','rs-report-table'),head=n('tr');for(const text of ['核保规则','本次状态','依据 / 限制'])head.append(n('th','',text));table.append(head);
 for(const row of result.rule_execution||[]){const tr=n('tr');tr.append(n('td','',row.name+(row.automatic?'（依赖）':'')),n('td','',statuses[row.status]||row.status),n('td','',row.reason));table.append(tr);}section.append(table);
 const detail=n('details');detail.append(n('summary','','查看参数与快照编号'));
 detail.append(n('p','uw-meta','快照：'+snapshot.snapshot_id));
 detail.append(n('p','',`模型参考分门槛：${snapshot.parameters?.environment_min_confidence}；照片时效：${snapshot.parameters?.watermark_days} 天；材料要求：${snapshot.parameters?.material_profile==='full_intake'?'完整':'基础'}`));
 const scopes=snapshot.selection?.scopes||{};for(const [key,value] of Object.entries(scopes)){const parts=[value.regions?.length?'地区：'+value.regions.join('、'):'',value.insured_types?.length?'投保人：'+value.insured_types.join('、'):'',value.min_capacity_kw!=null?'规模下限：'+value.min_capacity_kw+' kW':'',value.max_capacity_kw!=null?'规模上限：'+value.max_capacity_kw+' kW':''].filter(Boolean);if(parts.length)detail.append(n('p','',(snapshot.rules.find(r=>r.id===key)?.name||key)+'：'+parts.join('；')));}
 section.append(detail);report.append(section);
}
function renderCoverage(report,result){
 const a=result.evidence_coverage;if(!a?.version)return;
 const labels={satisfied:'已取得覆盖证据',insufficient:'需补材',review:'内部核验',not_selected:'未选择，未检查',not_applicable:'不适用',not_executed:'未执行'};
 const box=n('section','uw-section uw-evidence-coverage');box.append(n('h3','','材料覆盖与补拍清单'),n('p','uw-meta',a.notice),n('p','','客户补材 '+a.customer_requests.length+' 项 · 内部核验 '+a.review_questions.length+' 项；覆盖充分不代表风险检查通过。'));
 if(a.customer_requests.length){const requests=n('div');requests.append(n('h4','','需要补充什么'));for(const q of a.customer_requests)requests.append(n('p','',q));box.append(requests);}
 if(a.review_questions.length){const review=n('details');review.append(n('summary','','内部核验事项（不直接要求客户重拍）'));for(const q of a.review_questions)review.append(n('p','',q));box.append(review);}
 const detail=n('details');detail.append(n('summary','','查看各规则、安装区域与部位的覆盖依据'));
 const fileName=id=>(result.materials||[]).find(m=>m.material_id===id)?.file_name||id;
 for(const r of a.rows){const row=n('details');row.append(n('summary','',r.name+' · '+(r.area_id==='project'?'项目':r.area_id)+' · '+labels[r.status]),n('p','uw-meta',r.reason));
 for(const t of r.targets){row.append(n('h4','',t.label+' · '+labels[t.status]),n('p','',t.reason));for(const o of t.observations||[]){row.append(n('p','uw-meta',fileName(o.material_id)+'：'+({visible:'清楚可见',not_visible:'未拍到',occluded:'被遮挡',blurred:'模糊',unknown:'无法判断'}[o.status]||o.status)+'；'+o.evidence));}}
 for(const id of r.material_ids||[]){if(!result.case_id)continue;const link=n('a','','查看材料：'+fileName(id));link.href='/api/v1/cases/'+encodeURIComponent(result.case_id)+'/materials/'+encodeURIComponent(id)+'/source';link.target='_blank';link.rel='noopener';row.append(link,n('span','',' '));}
 detail.append(row);}
 box.append(detail);for(const d of a.duplicates||[])box.append(n('p','uw-meta','完全重复文件：'+fileName(d.material_id)+' → '+fileName(d.duplicate_of)+'；不增加关键部位覆盖。'));
 report.append(box);
}

function renderStrategy(report,a){
 if(!a?.scheme_snapshot)return;
 const s=a.scheme_snapshot,label=v=>({notify:'风险提示',none:'无命中',accept:'原 AI 建议通过',conditional_accept:'有条件通过',manual_review:'人工复核',request_more:'补材建议',recommend_reject:'拒保建议',surcharge:'加费建议（比例待业务确认）',unknown:'待核',low:'低',medium:'中',high:'高',critical:'极高',sufficient:'判断依据充分',incomplete:'判断依据不足'}[v]||v||'未记录');
 const box=n('section','uw-section');box.append(n('h3','','承保策略处理建议'+(s.demo?' · 演示方案':'')),n('p','',s.name+' · '+s.company+' · V'+s.version),n('p','','原 AI 建议：'+label(a.original_ai_decision)+'；策略处理建议：'+label(a.effective_suggestion)),n('p','uw-meta','策略关注等级：'+label(a.attention_level)+'（不改变计算风险等级）；最终决定须人工审核。'),n('p','',a.pricing.notice));
 for(const reason of a.reasons)box.append(n('p','',reason));
 const facts=n('details');facts.append(n('summary','','查看匹配事实与业务日期'),n('p','','省份：'+(a.facts.province||'未提供')+'；城市：'+(a.facts.city||'未提供')+'；精确型号：'+(a.facts.model||'尚未核验')+'；规模：'+(a.facts.capacity_kw??'未提供')+' kW'),n('p','','季节依据：拟起保日期 '+(a.facts.start_date||'未提供')+'，未使用操作当天代替。'),n('p','','风灾评级：'+label(a.facts.wind)+'；雪灾评级：'+label(a.facts.snow)+'；判断依据：'+label(a.facts.evidence)));box.append(facts);
 const details=n('details');details.append(n('summary','','策略命中、未命中与缺失依据 · '+a.rule_results.length+' 条'));
 const missing={provinces:'省份',cities:'城市',models:'已核验完整型号',proposed_start_date:'拟起保日期',installed_capacity_kw:'装机规模',wind:'风灾评级',snow:'雪灾评级',decision:'原 AI 建议',evidence:'判断依据'};
 for(const r of a.rule_results){const item=n('div');item.append(n('h4','',r.name+' · '+({matched:'命中',not_matched:'不匹配',insufficient:'依据不足'}[r.status])),n('p','','优先级 '+r.priority+'；动作：'+label(r.action)+(a.selected_ids.includes(r.rule_id)?'；列入最高优先级处理':'')+'；引用结果：'+label(r.observed_value)),n('p','','依据：'+r.reason),n('p','uw-meta','来源字段：'+r.evidence_ref+'；策略编号：'+r.rule_id));if(r.missing.length)item.append(n('p','','缺少：'+r.missing.map(k=>missing[k]||k).join('、')));details.append(item);}box.append(details);
 const audit=n('details');audit.append(n('summary','','业务依据与采用版本'),n('p','','依据：'+s.basis),n('p','','发布人：'+s.approved_by+'；版本编号：'+s.id),n('p','','有效期：'+s.valid_from+' 至 '+s.expires_at),n('p','','本次采用时间：'+s.selected_at+'；季节依据固定为拟起保日期。历史报告保留当时策略，不随新版本改写。'));box.append(audit);report.append(box);
}
function render(root,result,{resolveSource=()=>null,renderLocation=()=>{},demo=false,embedded=false,interpretation=false,reviewRevision=''}={}){
 const data=build(result),view=buildReportView(result,data),pendingIds=new Set(data.pending.filter(f=>f.category!=='image_quality').map(f=>f.finding_id)),displayedFindings=[...data.risks,...data.pending.filter(f=>f.category!=='image_quality')],groupedRisks=riskGroups(displayedFindings),missing=missingBasis(result,data),decision=result.decision||{}, materials=result.materials||[], name=id=>materials.find(m=>m.material_id===id)?.file_name||'未找到材料原件';
 const customerMissing=missing.filter(item=>view.actions.some(action=>action.owner==='customer'&&action.text===businessText(item.request)));
 const report=n('article','uw-report');root.append(report);
 if(result.verification?.schema){const entry=n('section','uw-section');entry.append(n('h3','','案件存疑点与整单核保决定'));const a=n('a','','进入逐项复核、补材及提交整单结论');a.href='/verification?case='+encodeURIComponent(result.case_id);entry.append(a,n('p','uw-meta','待处理事项 '+result.verification.issues.filter(i=>!['resolved','confirmed'].includes(i.status)).length+' 项。原始识别与人工承保意见分别保存。'));report.append(entry);}
 renderStrategy(report,result.strategy_assessment);
 renderCoverage(report,result);
 const local=result.component_review;
 if(local?.review_id){const section=n('section','uw-section');section.append(n('h3','','本案组件人工核验'),n('p','',local.applied?'本轮组件步骤已采用本案确认值；风雪计算是否完成请看后续评估。':'补充参数已确认，但本轮未执行组件步骤，不能据此认定已完成评估。'),n('p','uw-meta','仅本案使用，未写入共享参数库。核验人：'+local.reviewer_name+'；确认时间：'+local.confirmed_at),n('p','','完整型号：'+local.component_model+'；依据：'+local.model_reason),n('p','','补充／冲突处理理由：'+local.reason));
 for(const failure of local.ocr_failures||[])section.append(n('p','uw-meta',failure.reason));
 const labels={rated_power_w:'额定功率',front_static_load_pa:'正面机械载荷',back_static_load_pa:'背面机械载荷'},old=local.automatic_profile||local.original_profile||{};
 for(const [key,p]of Object.entries(local.parameters||{})){const detail=n('details');detail.append(n('summary','',labels[key]+'：原值 '+(old[key]??'未取得')+' → 本案确认 '+p.normalized_value+' '+p.normalized_unit),n('p','','原始输入：'+p.value+' '+p.unit+'；口径：'+({test:'测试载荷',design:'设计载荷',unspecified:'待确认'}[p.load_basis])),n('p','','来源：'+p.document_title+' · '+p.document_version+' · '+p.page),n('p','','原文：'+p.quote),n('p','','适用条件：'+p.applicability+'；现场适用性'+(p.installation_confirmed?'已确认':'待核验')));
 if(p.source_material_id){const a=n('a','','查看本轮厂家资料');a.href='/api/v1/cases/'+encodeURIComponent(result.case_id)+'/materials/'+encodeURIComponent(p.source_material_id)+'/source';a.target='_blank';a.rel='noopener';detail.append(a);}if(/^https:\/\//i.test(p.source_url||'')){const a=n('a','','查看原始来源链接');a.href=p.source_url;a.target='_blank';a.rel='noopener';detail.append(a);}section.append(detail);}
 const raw=n('details');raw.append(n('summary','','查看保留的原始自动查询与冲突记录'),n('pre','',JSON.stringify(local.automatic_result||local.original_result||{},null,2)));section.append(raw);report.append(section);}

 const reused=result.component_module_result?.verified_reuse||result.component_profile?.workbench_result?.verified_reuse;
 if(reused){const audit=n('section','uw-section');audit.append(n('h3','','已核验参数复用 · V'+reused.version),n('p','',reused.notice),n('p','uw-meta','型号：'+reused.component_model+' · 核验人：'+reused.approved_by+' · 记录：'+reused.id),n('p','uw-meta','本轮采用时间：'+reused.used_at+' · 当时有效期截止：'+reused.expires_at),n('p','','来源适用条件：'+reused.applicability),n('p','uw-meta','本处保留本轮采用时的快照；当前状态请在已核验参数库查看。'));const link=n('a','','查看参数库当前状态');link.href='/verified-components';audit.append(link);report.append(audit);}

 const gate=result.package_assessment;
 if(!embedded){const header=n('header','uw-header');const heading=n('div');heading.append(n('div','uw-eyebrow',demo?'版式预览 · 示例数据，非真实核保结论':'核保审阅报告'),n('h2','',result.project?.project_name||'光伏项目 · 材料审查'));header.append(heading,n('span','uw-meta',[result.case_id,result.project?.insured_name,result.project?.site_address,result.project?.proposed_start_date].filter(Boolean).join(' · ')||'未提供案件编号'));report.append(header);}
 const needsMaterials=decision.decision==='request_more';
 const decisionTone=decisions[decision.decision]?decision.decision:'unknown';
 const hero=n('section','uw-decision uw-decision--'+decisionTone);hero.append(n('div','uw-eyebrow','系统建议 · 待核保人员审阅'),n('h3','',decisionLabel(decision)));
 hero.append(n('p','uw-workflow-state',view.progression+' · '+view.workflowAction));
 if(needsMaterials){
  hero.append(n('h4','uw-decision-list-title',`待补材料 · ${customerMissing.length}`));
  const list=n('ol','uw-missing-list');hero.append(list);
  customerMissing.forEach(item=>{const card=n('li','uw-missing-item');card.append(n('h4','',item.name.replace(/^(补充|补拍|重拍)/,'')));if(item.blocked&&!item.blocked.startsWith('报告尚未写明'))card.append(n('p','uw-missing-blocked',item.blocked));list.append(card);});
  hero.append(n('p','uw-blocked-operation',result.ruleset_snapshot?.rules?.find(r=>r.id==='wind_snow')?.status==='not_selected'?'本次未选择风雪筛查；补材后重查所选规则。':(result.catastrophe_assessment||result.pv_load_screening_assessment)?'本轮曾运行抗灾筛查；材料补齐后需重新检查，现有筛查结果不作为承保依据。':'材料未齐，本轮不进行风、雪抗灾筛查。'));
 }
 const keyBasis=n('div','uw-key-basis');keyBasis.id='uw-basis';keyBasis.append(n('strong','','关键依据'));
 if(!view.basis.length)keyBasis.append(n('p','uw-empty','本轮未保存可核对的建议依据，请查看完整记录。'));
 const addBasisRow=(target,item)=>{
  const row=n('div','uw-key-basis-item');row.basisText=item.text;row.id=refDomId('basis:'+String(view.basis.indexOf(item)+1));row.append(n('span','',item.text),n('small','',item.impact));
  const linked=basisEvidence(item,result),detail=n('details','uw-basis-source uw-key-basis-source');
  detail.append(n('summary','',linked.length?'展开查看对应原图与解释':'展开查看依据'));
  if(linked.length)linked.forEach((evidence,index)=>renderDecisiveEvidence(detail,evidence,index,{name,resolveSource}));
  else detail.append(n('p','uw-empty','这项依据没有关联的现场图片，请核对上方说明及原始项目资料。'));
  row.append(detail);target.append(row);
 };
 view.basis.slice(0,3).forEach(item=>addBasisRow(keyBasis,item));
 if(view.basis.length>3){const more=n('details','uw-more-basis');more.append(n('summary','',`展开其余 ${view.basis.length-3} 项依据`));view.basis.slice(3).forEach(item=>addBasisRow(more,item));keyBasis.append(more);}
 if(!needsMaterials)hero.append(keyBasis);
 const allWarnings=[...new Set(decision.warnings||[])];
 const notices=allWarnings.filter(text=>warningRule(text,result)==='DOC-COMPARISON-FIELD-UNAVAILABLE-001');
 const warningEntries=groupedWarningEntries(allWarnings.filter(text=>!notices.includes(text)),result,new Set(displayedFindings.map(f=>f.finding_id)));
 const catastropheFactors=new Set(result.catastrophe_assessment?.factors||[]);
 const isCatastropheWarning=entry=>entry.raw.some(raw=>catastropheFactors.has(raw)||/抗灾|风雪|风灾|雹灾|雪灾|地面粗糙度|组件厂家参数|组件参数.*安装条件|历史气象/.test(raw));
 const catastropheEntries=warningEntries.filter(isCatastropheWarning),ordinaryWarnings=warningEntries.filter(entry=>!isCatastropheWarning(entry));
 const warningCount=ordinaryWarnings.length+(catastropheEntries.length?1:0);
 const isRejectReview=item=>item.text==='核对拒保原件、现场关系与适用规则';
 const currentActions=decision.decision==='recommend_reject'?view.actions.filter(isRejectReview):view.actions;
 const deferredActions=decision.decision==='recommend_reject'?view.actions.filter(item=>!isRejectReview(item)):[];
 if(!needsMaterials&&currentActions.length){const next=n('div','uw-next-summary');next.append(n('strong','','下一步'));for(const item of currentActions.slice(0,2))next.append(n('p','',item.text));if(currentActions.length>2){const more=n('details','uw-more-actions');more.append(n('summary','',`展开另外 ${currentActions.length-2} 项待核事项`));for(const item of currentActions.slice(2))more.append(n('p','',item.text));next.append(more);}hero.append(next);}report.append(hero);
 renderRuleScope(report,result);
 if(interpretation&&!demo&&result.decision)renderInterpretation(report,result,{reviewRevision});
 if(!embedded)renderReportProgress(report,result,customerMissing.length);
 function section(title,items,cls,empty){const s=n('section','uw-section '+(cls||''));s.append(n('h3','',title+' · '+items.length));if(!items.length)s.append(n('p','uw-empty',empty));report.append(s);return s;}
 function evidence(parent,f,kind){const card=n('article','uw-evidence');if(f.finding_id)card.id=refDomId('finding:'+f.finding_id);const top=n('div','uw-evidence-head');top.append(n('strong','',f.label||f.category),n('span','uw-badge '+kind,kind==='reject'?'触发拒保规则':kind==='pending'?'证据不足 / 待核实':'风险提示'));card.append(top,n('div','uw-meta','来源：'+name(f.material_id)),n('p','',f.evidence_text||'未提供可观察证据，需要核对原图。'));
  const detail=n('details','uw-source');detail.append(n('summary','',f.bbox?'查看原图及模型定位':'查看原图（无可靠定位框）'));let loaded=false;detail.addEventListener('toggle',()=>{if(!detail.open||loaded)return;loaded=true;const source=resolveSource(f.material_id);if(!source?.url){detail.append(n('p','uw-empty','当前记录未保存可用原件，请从原始材料中核对。'));return;}if(source.pdf){const iframe=n('iframe');iframe.src=source.url;iframe.title=name(f.material_id);detail.append(iframe);return;}
   const wrap=n('div','uw-image');const img=n('img');img.src=source.url;img.alt=name(f.material_id);wrap.append(img);const b=f.bbox;if(b&&b.coordinate_space==='normalized_0_1'&&[b.x_min,b.y_min,b.x_max,b.y_max].every(x=>Number.isFinite(x)&&x>=0&&x<=1)&&b.x_max>b.x_min&&b.y_max>b.y_min){const box=n('div','uw-box '+kind);box.style.cssText=`left:${b.x_min*100}%;top:${b.y_min*100}%;width:${(b.x_max-b.x_min)*100}%;height:${(b.y_max-b.y_min)*100}%`;wrap.append(box);}detail.append(wrap,n('p','uw-meta',kind==='pending'?'虚线框表示待核实区域；未显示的区域不能据此认定无风险。':'框仅标示模型返回的证据位置，需核保人员核对。'));
  });card.append(detail);parent.append(card);
 }
 const findingsById=new Map((result.findings||[]).map(item=>[item.finding_id,item]));
 const fieldsById=new Map((result.ocr_fields||[]).map(item=>[item.field_id,item]));
 function openRiskDialog(title,explanation,items,emptyExplanation){
  const dialog=n('dialog','uw-risk-dialog');const head=n('div','uw-risk-dialog-head'),close=n('button','uw-risk-dialog-close','关闭');close.type='button';head.append(n('h3','',title),close);dialog.append(head);
  for(const line of Array.isArray(explanation)?explanation:[explanation])dialog.append(n('p','uw-risk-dialog-explanation',line));
  if(items.length)items.forEach((item,index)=>renderDecisiveEvidence(dialog,item,index,{name,resolveSource}));
  else dialog.append(n('p','uw-empty',emptyExplanation||'此提示来自文字、位置、气象或规则资料；本轮没有可对应的已标记现场图片，请按上方解释及原始资料核对。'));
  const dismiss=()=>{if(typeof dialog.close==='function')dialog.close();else dialog.open=false;};
  close.addEventListener('click',dismiss);dialog.addEventListener('close',()=>{if(typeof dialog.remove==='function')dialog.remove();});
  report.append(dialog);if(typeof dialog.showModal==='function')dialog.showModal();else dialog.open=true;
 }
 function appendMarkedEvidence(parent,{findingIds=[],ocrFieldIds=[],materialIds=[],key=null},state='risk'){
  const relevant=checkRiskCategories[key];
  const selected=findingIds.map(id=>findingsById.get(id)).filter(f=>f&&(state==='risk'?f.detection_status==='detected'&&f.category!=='image_quality'&&(!relevant||relevant.has(f.category)):f.detection_status==='uncertain'||f.category==='image_quality'&&f.detection_status==='detected'));
  const fieldItems=ocrFieldIds.map(id=>fieldsById.get(id)).filter(field=>field?.filing_assessment?.status==='confirmed');
  const items=selected.map(f=>({kind:state==='risk'?'single':'review',materialId:f.material_id,title:f.label||(state==='risk'?'现场风险':'待核画面'),explanation:f.evidence_text,bbox:f.bbox||null,ruleId:findingRule(f)}));
  for(const field of fieldItems)items.push({kind:'filing',materialId:field.material_id,title:businessText(field.field_name),explanation:field.filing_assessment.reason||field.evidence_text||'请核对原文',quote:field.raw_value||field.normalized_value||null,fragment:field.filing_assessment.evidence_quote||null,bbox:field.filing_assessment.evidence_bbox||field.bbox||null,page:field.page_number});
  if(!items.length)for(const materialId of materialIds)items.push({kind:'review',materialId,title:name(materialId),explanation:'本轮未保存可靠定位框，请核保员查看完整原件。',bbox:null});
  if(!items.length)parent.append(n('p','uw-empty','本轮未记录可对应的原件，请核对材料清单。'));
  items.forEach((item,index)=>renderDecisiveEvidence(parent,item,index,{name,resolveSource}));
 }
 const renderAction=(parent,item)=>{const row=n('article','uw-action-item');row.id=refDomId(item.id);row.actionText=item.text;row.append(n('strong','',item.text),n('p','',`当前影响：${item.blocked}`),n('p','uw-meta',`处理后：${item.after}`));parent.append(row);};
 if(!needsMaterials&&currentActions.length){const actionSection=n('details','uw-section uw-actions');actionSection.id='uw-actions';actionSection.append(n('summary','','待核事项明细 · '+currentActions.length));for(const item of currentActions)renderAction(actionSection,item);report.append(actionSection);}
 if(!needsMaterials&&(!result.ruleset_snapshot?.snapshot_id||result.ruleset_snapshot.rules.some(r=>r.id==="wind_snow"&&r.status==="active")))renderScreening(report,result);
 else if(result.component_profile)renderComponentModule(report,result);
 if(groupedRisks.length||warningCount){
  const risk=section('风险提示与待核事项',Array(groupedRisks.length+warningCount),'uw-risk-section');
  for(const group of groupedRisks){const hasPending=group.items.some(f=>pendingIds.has(f.finding_id)||(result.stage_reviews||[]).some(stage=>stage.action==='manual_review'&&(stage.finding_ids||[]).includes(f.finding_id))),isShading=['minor_shading','severe_shading'].includes(group.key),title=group.items.length>1?String(group.title).replace(/检查$/,''):group.title,photoCount=new Set(group.items.map(f=>f.material_id)).size;const block=n('button','uw-risk-group uw-risk-trigger'+(hasPending?' uw-pending-trigger':'')+(isShading?' uw-shading-trigger':''));block.type='button';block.append(n('strong','',title),n('span','uw-meta',`${photoCount} 张照片${hasPending?' · 含待复核结果':''} · 点击查看`));block.addEventListener('click',()=>openRiskDialog(title,'请逐张核对模型标记位置和风险解释；各照片的判断状态分别保留。',group.items.map(f=>({kind:pendingIds.has(f.finding_id)?'review':'single',materialId:f.material_id,title:f.label||title,explanation:f.evidence_text||'模型未提供具体说明',bbox:f.bbox||null,ruleId:findingRule(f),refId:'finding:'+f.finding_id}))));risk.append(block);}
  const list=n('div','uw-risk-notice-list');risk.append(list);
  for(const entry of ordinaryWarnings){const item=n('button','uw-warning-item uw-risk-trigger');item.type='button';item.append(n('p','',entry.label),n('span','uw-meta',entry.details.length?`${entry.details.length} 项说明 · 点击查看依据`:'点击查看依据'));item.addEventListener('click',()=>openRiskDialog(entry.label,entry.details.length?entry.details:entry.label,warningEvidence(entry.raw,result),entry.raw.some(missingStartDateWarning)?'缺少的是投保项目资料中的拟起保日期；照片即使清晰，也无法单独证明拍摄时间符合本次规则设置的有效期要求。填写日期后需重新核验时效。':null));list.append(item);}
  if(catastropheEntries.length){const raw=catastropheEntries.flatMap(entry=>entry.raw),labels=catastropheEntries.map(entry=>entry.label);const item=n('button','uw-warning-item uw-risk-trigger');item.type='button';item.append(n('p','','抗灾能力量化筛查'),n('span','uw-meta',`${labels.length} 项提示 · 点击逐项查看`));item.addEventListener('click',()=>openRiskDialog('抗灾能力量化筛查',labels,warningEvidence(raw,result)));list.append(item);}
 }
 if(decision.conditions?.length){const s=section('承保条件',decision.conditions);decision.conditions.forEach(c=>s.append(n('p','',c)));}
 if(result.location_assessment?.status==='conflict'){const location=n('section','uw-section uw-location-review');report.append(location);renderLocation(location,result.location_assessment,result.case_id);}
 report.append(n('footer','uw-footer','对于未识别到风险的图片，结论仅限当前图像和模型的识别范围，不代表现场无风险。系统建议供核保审阅，最终结论以核保人员审核为准。'));return data;
}
global.UnderwritingReport={build,buildReportView,missingBasis,warningRule,decisiveEvidence,decisiveGroups,riskGroups,basisEvidence,render,screeningData,weatherQueryReasons,paddedEvidenceBox};if(typeof module!=='undefined')module.exports=global.UnderwritingReport;
})(typeof window==='undefined'?globalThis:window);
