"""Versioned common rules, material instructions and one output contract."""
import json
from pathlib import Path

from app.contracts import MaterialCategory, RiskCategory
from app.contracts.enums import RETIRED_RISK_CATEGORIES
from app.rules.engine import ADVANCED_CHECKS_BY_MATERIAL, EXPECTED_CHECKS_BY_MATERIAL
from app.prompts.simulation import ensure_simulation_rule

ROOT = Path(__file__).parent
VERSION = 'v4 · 通用水印与业务文字分离'


def expected_checks(category):
    return sorted(c.value for c in (
        EXPECTED_CHECKS_BY_MATERIAL.get(category, frozenset())
        | ADVANCED_CHECKS_BY_MATERIAL.get(category, frozenset())
    ))


def builtin_bundle():
    return {'version_name': VERSION, 'prompt': (ROOT / 'common.txt').read_text(encoding='utf-8'),
            'specialized_prompts': {c.value: (ROOT / f'{c.value}.txt').read_text(encoding='utf-8')
                                    for c in MaterialCategory if (ROOT / f'{c.value}.txt').exists()}}


def load_bundle(path):
    bundle = builtin_bundle()
    if path.is_file():
        saved = json.loads(path.read_text(encoding='utf-8'))
        bundle.update({k: saved[k] for k in ('prompt', 'version_name') if k in saved})
        bundle['specialized_prompts'].update(saved.get('specialized_prompts', {}))
        if '【水印】' in bundle['prompt']:
            before, section = bundle['prompt'].split('【水印】',1)
            if '只有 panorama' in section:
                bundle['prompt'] = before+'【水印】\n'+(ROOT/'common.txt').read_text(encoding='utf-8').split('【水印】\n',1)[1]
        if bundle['version_name'] in {'v2 · 按材料组合','v3 · 照片与文档分流'}:
            bundle['version_name'] = VERSION
    return bundle


def output_contract():
    return (ROOT / 'output.txt').read_text(encoding='utf-8') + '\n允许的 category：' + '、'.join(
        c.value for c in RiskCategory if c not in RETIRED_RISK_CATEGORIES
    )


def compose_prompt(common, specialized, category):
    return ensure_simulation_rule('\n\n'.join([common.strip(),
        f'【当前材料】{category.value}\n【必检清单】' + '、'.join(expected_checks(category)),
        specialized.strip(), output_contract()]))
