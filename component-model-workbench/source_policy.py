"""Explicit publisher admission; search titles cannot establish official identity."""
import json
import re
from pathlib import Path
from urllib.parse import urlparse, unquote


def domain_matches(host, domain):
    return host == domain or host.endswith('.' + domain)


class SourcePolicy:
    def __init__(self, manufacturer, payload=None):
        self.manufacturer = manufacturer
        self.payload = payload or json.loads((Path(__file__).parent/'data/source_policy.json').read_text('utf-8'))
        self.domains = self.payload['manufacturer_official'].get(manufacturer, [])
        self.version = self.payload['version']

    def classify(self, url):
        base = {'policy_version': self.version, 'publisher': None, 'source_tier': None, 'source_class': None}
        def excluded(reason):
            return {**base, 'allowed': False, 'reason': reason}
        try:
            p = urlparse(url)
            host = (p.hostname or '').lower().rstrip('.')
            if p.scheme != 'https' or not host or p.username or p.password or p.port not in (None,443):
                return excluded('仅接受无凭据的公开HTTPS资料链接')
        except ValueError:
            return excluded('链接格式无效')
        if any(domain_matches(host,d) for d in self.payload['blocked_platforms']):
            return excluded('排除个人发表平台、用户内容平台及交易平台')
        if re.search(self.payload.get('excluded_host_pattern',r'(?!)'),host,re.I):
            return excluded('排除用户社区及论坛子域名')
        if re.search(self.payload['excluded_path_pattern'], unquote(p.path), re.I):
            return excluded('排除论坛、用户主页、讨论与评论区')
        if any(domain_matches(host,d) for d in self.domains):
            return {**base, 'allowed': True, 'publisher': self.manufacturer, 'source_tier': 'manufacturer', 'source_class': 'manufacturer_official', 'reason': '已登记的当前厂商官网域名'}
        for org in self.payload['institutions']:
            if any(domain_matches(host,d) for d in org['domains']):
                return {**base, 'allowed': True, 'publisher': org['publisher'], 'source_tier': 'institution', 'source_class': org['kind'], 'reason': '已登记的机构或新闻机构官方网站'}
        return excluded('发布主体未核验，不作为官网或机构资料采信')

    def require(self, url, phase):
        decision = self.classify(url)
        if not decision['allowed'] or decision['source_tier'] != phase:
            from pv_component_lookup.contracts import ProviderError
            raise ProviderError('来源策略阻止读取：' + decision['reason'])
        return decision

    def summary(self):
        return {'version': self.version, 'order': ['manufacturer','institution'], 'personal_sources_allowed': False,
                'official_domains': self.domains, 'official_domains_registered': bool(self.domains),
                'unknown_sources': 'exclude_until_verified'}
