"""Rule-linked coverage of required evidence, never an inference of intent."""
from copy import deepcopy
from app.rules.quality_evidence import quality_evidence_conflicts

VERSION = '1'
NOTICE = '覆盖仅针对本轮可见证据，不证明全部现场或材料真实性；材料缺失不等于故意隐瞒。'
# Each target is assessed independently. More images of one part cannot fill another.
def target(id_, label, risks, request):
    return dict(id=id_, label=label, risks=risks.split(), request=request)

REQUIREMENTS = {
    'environment': [target('site_surroundings','阵列与周边场地关系','agriculture_environment forest_environment livestock_environment fishery_environment water_adjacent_environment tidal_flat_environment mountain_environment desertification_environment','补拍含光伏阵列、场地边界及周边环境的连贯全景，说明近邻与项目场地的关系。')],
    'shading': [target('array_shading','组件表面与遮挡物关系','severe_shading minor_shading','补拍组件整面及附近遮挡物的位置关系；遮挡处增加侧面视角。')],
    'connection': [target('support_context','组件、支架与承载结构关系','roof_connection_abnormal','补拍组件、支架与建筑屋面或车棚梁柱的整体连接关系。'),target('connection_node','连接节点与紧固件','roof_connection_abnormal','近距离拍清连接节点、螺栓或夹具，避开遮挡，并保留可定位的整体照片。'),target('anchorage','锚固或基础连接部位','roof_connection_abnormal','拍清支架与屋面或基础的固定部位；隐蔽连接请提供施工记录或核验资料供人工核查。')],
    'edge': [target('edge_protection','边缘保护部位','missing_parapet_or_guardrail','补拍适用区域的屋面边缘、女儿墙或护栏与阵列的位置关系；不适用需说明依据。'),target('drainage','排水部位','drainage_abnormal','补拍排水口、排水通路及附近积水或堵塞情况。')],
    'workshop': [target('workshop_storage','屋顶下车间及存放区域','flammable_material hazardous_material dangerous_process cleanroom fire_protection_absent','补拍对应屋顶下的生产、存放区域及出入口，覆盖可燃物、危险品存放位置；无法进入时提供核查说明。')],
    'grounding': [target('ground_connection','接地连接处','electrical_grounding_abnormal','拍清接地线与设备、接地端子的连接处；照片不能替代接地检测记录。'),target('cable_route','线缆敷设与保护','unprotected_cable','补拍线缆走向、穿管及接头保护处。')],
    'combiner': [target('box_seal','箱体与密封','combiner_box_seal_abnormal','补拍汇流箱箱体、门封及进出线密封处。'),target('box_fuse','熔断器','combiner_box_fuse_abnormal','由具备现场作业条件的人员提供熔断器可辨认照片或检修记录，不要求客户带电开箱。'),target('box_surge','防雷器窗口','surge_protector_abnormal','补拍防雷器状态窗口和标签；不能安全拍摄时提供检修记录供核验。')],
    'surface': [target('module_surface','组件表面','module_damage module_discoloration','补拍各安装区域的组件表面全貌，对疑似破损或变色处补充清晰近照。')],
    'inverter': [target('inverter_body','逆变器外观','inverter_abnormal','补拍逆变器外壳、安装位置及可见接线部位，并配套可读铭牌。')],
}
GUIDANCE = {
    'filing':'提供可读备案证明，含项目名称、地址、建设内容及主体。',
    'installation':'提供阵列、承载结构及下方用途的整体视角，区分不同安装区域。',
    'aisle':'提供可定位的检修通道全貌及两端走向，避免只拍组件近景。',
    'watermark':'提供带可核对拍摄日期和位置的原图，并填写拟起保日期；无水印应提供替代核验依据。',
    'location':'提供备案地址与现场位置的对应依据，坐标、地址或照片不一致时由人员核验。',
    'component':'提供完整型号可读的组件铭牌或设备清单；缺失参数附厂家资料，标明对应型号及来源。',
    'wind_snow':'提供确认的位置、完整组件型号及对应参数；安装几何和适用条件不明确时需人工核验。',
}

def requirements_for(rule):
    return deepcopy(REQUIREMENTS.get(rule['id'], []))

def guidance_for(rule):
    return [t['request'] for t in REQUIREMENTS.get(rule['id'], [])] or [GUIDANCE.get(rule['id'],'提供可定位、可读且与本案相符的证明材料。')]

def prompt_for(checks, rules):
    allowed={c.value for c in checks}
    lines=[]
    for rule in rules:
        if rule['status']!='active':continue
        for t in rule.get('evidence_targets', []):
            relevant=allowed & set(t['risks'])
            if relevant:lines.append(f"{','.join(sorted(relevant))} → {t['id']}：{t['label']}；{t['request']}")
    if not lines:return ''
    return '''\n【逐部位证据覆盖】
对下列检查在对应 finding 中填写 evidence_coverage 数组，每项包含 target、status、evidence_text。
status 仅用 visible（部位清楚可辨）、not_visible（未拍到）、occluded（被遮挡）、blurred（模糊）、unknown（无法判断）。
visible 必须具体描述看到的部位及其关系，不能用“未发现风险”代替可见证据。设施不存在不等于未拍到：若现场完整可见但设施缺失，覆盖仍可为 visible，风险另行判断。
不得由照片类别、张数或同一部位的重复照片推定覆盖。每张只评价本张，不借用其他图。未观察到风险不代表证据充分；不根据漏拍推断投保人意图。
未列入的部位不输出。无匹配项时 evidence_coverage 返回 []。
'''+'\n'.join(lines)


def assess(case):
    snapshot=case.ruleset_snapshot
    if snapshot.get('evidence_coverage_version')!=VERSION:return {}
    materials={m.material_id:m for m in case.materials}
    executions={r['id']:r for r in case.rule_execution}
    failed={m for t in case.processing_trace for m in t.failed_material_ids}
    rows=[]
    for rule in snapshot['rules']:
        execution=executions.get(rule['id'],{})
        base=dict(rule_id=rule['id'],name=rule['name'],area_id='project',targets=[],material_ids=[],finding_ids=[],customer_requests=[],review_questions=[])
        state=rule['status']
        if state!='active' or execution.get('status')=='not_applicable':
            status='not_selected' if state=='not_selected' else 'not_applicable' if state=='not_applicable' or execution.get('status')=='not_applicable' else 'review'
            rows.append(dict(base,status=status,reason=execution.get('reason') or rule['reason'],review_questions=[rule['reason']] if status=='review' else []));continue
        # An intentional early stop must remain distinct from missing evidence.
        if case.pre_catastrophe_outcome=='recommend_reject' and execution.get('status')=='not_executed':
            rows.append(dict(base,status='not_executed',reason='前置风险已触发停止，本项覆盖未核验。'));continue
        targets=rule.get('evidence_targets',[])
        if not targets:
            status=execution.get('status','not_executed')
            mapped={'completed':'satisfied','risk_found':'satisfied','material_missing':'insufficient','evidence_insufficient':'review'}.get(status,'review')
            rows.append(dict(base,status=mapped,reason=execution.get('reason','未取得本项核验结果'),material_ids=execution.get('material_ids',[]),customer_requests=rule.get('evidence_guidance',[]) if mapped=='insufficient' else [],review_questions=[execution.get('reason','本项尚未执行，需内部核验')] if mapped=='review' else []));continue
        areas=list(case.installation_analysis.areas) if case.installation_analysis else []
        # Site-wide environmental relationship is a project check; equipment closeups
        # cannot be assigned to every roof merely because they were uploaded together.
        scoped=areas if rule['id'] in {'shading','connection','edge','workshop','surface'} and areas else [None]
        for area in scoped:
            row=deepcopy(base);row['area_id']=area.area_id if area else 'project'
            scene_checks=[c for c in case.applicable_checks if c.area_id==row['area_id'] and c.key in rule['checks']]
            if scene_checks and all(c.applicability=='not_applicable' for c in scene_checks):
                rows.append(dict(row,status='not_applicable',reason='；'.join(c.reason for c in scene_checks)));continue
            area_pending=area is not None and area.status!='confirmed' or any(c.applicability=='pending' for c in scene_checks)
            candidates=[m for m in case.materials if not m.duplicate_of_material_id and (m.category.value in rule['materials'] or any(c.value in rule['materials'] for c in m.additional_categories)) and (area is None or m.material_id in area.material_ids)]
            if area is not None and not candidates:
                assigned={mid for a in areas for mid in a.material_ids}
                unassigned=[m for m in case.materials if not m.duplicate_of_material_id and m.material_id not in assigned and (m.category.value in rule['materials'] or any(c.value in rule['materials'] for c in m.additional_categories))]
                if unassigned:
                    area_pending=True
                    row['review_questions'].append('已有相关材料尚未归属具体安装区域，先核对照片与区域对应关系。')
            ids={m.material_id for m in candidates};row['material_ids']=sorted(ids)
            fs=[f for f in case.findings if f.material_id in ids]
            conflicts=any(quality_evidence_conflicts(f) for f in fs)
            if area_pending:
                row['review_questions'].append('先确认该安装区域、用途和检查适用性，不直接要求补拍不适用设施。')
            if conflicts:row['review_questions'].append('图片质量状态与文字证据矛盾，请内部重试或核验原图。')
            for target in targets:
                # Disabled advanced checks do not introduce extra requirements.
                expected={v for values in snapshot.get('evidence_expected_risks',{}).values() for v in values}
                if not set(target['risks']) & expected:continue
                obs=[]
                for f in fs:
                    if f.category.value not in target['risks']:continue
                    for o in f.evidence_coverage:
                        if o.target==target['id']:obs.append((f,o))
                detail=dict(target_id=target['id'],label=target['label'],status='review',reason='',material_ids=[],finding_ids=[],observations=[dict(material_id=f.material_id,finding_id=f.finding_id,status=o.status,evidence=o.evidence_text) for f,o in obs],customer_request=None)
                by_file={mid:{o.status for f,o in obs if f.material_id==mid} for mid in ids}
                inconsistent=any('visible' in vals and len(vals)>1 for vals in by_file.values())
                valid=[(f,o) for f,o in obs if f.confidence>=.65 and f.material_id not in failed and materials[f.material_id].quality_status.value=='usable' and materials[f.material_id].parse_status.value=='success' and not any(q.category.value=='image_quality' and q.material_id==f.material_id and q.detection_status.value=='detected' for q in fs)]
                visible=[(f,o) for f,o in valid if o.status=='visible']
                gaps=[(f,o) for f,o in valid if o.status in {'not_visible','occluded','blurred'}]
                if conflicts or area_pending or inconsistent:
                    detail['reason']='存在识别矛盾或区域适用性待核，先内部核验。'
                elif visible:
                    detail.update(status='satisfied',reason='本部位已取得清楚可见的证据；不等于该风险检查通过。',material_ids=sorted({f.material_id for f,o in visible}),finding_ids=sorted({f.finding_id for f,o in visible}))
                elif not candidates:
                    detail.update(status='insufficient',reason='未收到归属本检查区域的对应材料。',customer_request=target['request'])
                elif ids & failed:
                    detail['reason']='对应材料识别失败，请内部重试或人工核验；不据此要求客户重拍。'
                elif gaps:
                    detail.update(status='insufficient',reason='；'.join(dict.fromkeys(o.evidence_text for f,o in gaps)),material_ids=sorted({f.material_id for f,o in gaps}),finding_ids=sorted({f.finding_id for f,o in gaps}),customer_request=target['request'])
                elif all(m.quality_status.value=='unusable' or m.parse_status.value!='success' for m in candidates) or any(f.category.value=='image_quality' and f.detection_status.value=='detected' for f in fs):
                    detail.update(status='insufficient',reason='所需部位没有可读证据，已提交文件存在画质或读取问题。',customer_request=target['request'])
                else:
                    detail['reason']='未返回可靠的逐部位覆盖结果或参考分不足；不能凭“未发现风险”视为已拍清，请内部重试或人工核验。'
                row['targets'].append(detail)
                row['finding_ids'].extend(detail['finding_ids'])
                if detail['customer_request']:row['customer_requests'].append(f"{rule['name']}（{row['area_id']}）—{detail['customer_request']}")
                elif detail['status']=='review':row['review_questions'].append(target['label']+'：'+detail['reason'])
            if execution.get('status')=='evidence_insufficient' and not row['review_questions']:
                row['review_questions'].append(execution.get('reason','风险判断仍需人工核验。'))
            row['status']='insufficient' if row['customer_requests'] else 'review' if row['review_questions'] else 'satisfied' if row['targets'] else 'not_applicable'
            row['reason']='按关键部位核验，不以照片数量替代覆盖。'
            rows.append(row)
    duplicates=[dict(material_id=m.material_id,duplicate_of=m.duplicate_of_material_id) for m in case.materials if m.duplicate_of_material_id]
    return dict(version=VERSION,notice=NOTICE,rows=rows,duplicates=duplicates,
        customer_requests=list(dict.fromkeys(q for r in rows for q in r['customer_requests'])),
        review_questions=list(dict.fromkeys(q for r in rows for q in r['review_questions'])))
