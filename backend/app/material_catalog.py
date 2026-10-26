"""Shared photo/document categories for classification and both workbenches."""
from app.contracts import MaterialCategory

MATERIAL_CATALOG = {
 'panorama': ('电站全景照', '光伏阵列与整体场地，侧重周边环境和阵列布局'),
 'roof_connection': ('屋顶连接处', '组件支架与屋面连接、锚固节点的细节'),
 'parapet': ('女儿墙 / 护栏', '屋顶边缘女儿墙、护栏或排水设施'),
 'workshop': ('车间内部', '厂房内部生产或储存场所'),
 'project_document': ('项目证照／协议', '电站备案证、并网许可或调度协议首页；依据可读标题及项目字段识别'),
 'electrical_grounding': ('电气接地', '接地排、连接、防雷、线缆敷设或接地检测记录'),
 'component_nameplate': ('组件铭牌', '光伏组件型号、功率等铭牌标签'),
 'component_surface': ('组件表面', '组件正面或背板近照'),
 'inverter_nameplate': ('逆变器铭牌', '逆变器设备或其铭牌，不能与组件铭牌混淆'),
 'combiner_box': ('汇流箱', '汇流箱外观、内部熔断器或防雷器'),
 'monitoring_optional': ('监控区域', '监控画面、摄像头或覆盖示意'),
 'equipment_inventory': ('设备清单', '列出设备名称、规格型号、数量等列的清单或明细表；单个设备铭牌不属于设备清单'),
 'other': ('其他材料', '无关内容、模糊或无法确定用途的图片'),
}
DOCUMENT_CATEGORIES = frozenset({MaterialCategory.PROJECT_DOCUMENT, MaterialCategory.FILING_CERTIFICATE, MaterialCategory.GRID_CONNECTION_DOCUMENT, MaterialCategory.EQUIPMENT_INVENTORY})

def canonical_category(category):
 value = category.value if isinstance(category, MaterialCategory) else category
 return 'project_document' if value in {'filing_certificate','grid_connection_document'} else value
