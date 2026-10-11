"""精确核验的来源公告版本身份；保存两版不表示账本已支持该行动。"""
from hashlib import sha256
import json

from .config import ROOT


ZHONGYAN_REVISIONS = {
    '3f6a014c40491dd8e8d29028af5229c0b9ce4c406ad113884e0446c429f99cac': {
        'label':'original_20250630','announcement_date':'2025-06-30','announcement':'2025-055',
        'document':'docs/research/zhongyan-2025-dividend-original.pdf',
        'source_url':'https://static.cninfo.com.cn/finalpage/2025-06-30/1224017153.PDF',
        'sha256':'972fa64793d6feb1486d0f88f7d93e4bb2fc93bf335c69ea63fe650bdf66c66a',
    },
    '694c27d43a47c6c7e0b91c51cd2eeba49b7a7b5461a716ed85a0b039ad180052': {
        'label':'adjusted_20250705','announcement_date':'2025-07-05','announcement':'2025-057',
        'document':'docs/research/zhongyan-2025-dividend-adjusted.pdf',
        'source_url':'https://static.cninfo.com.cn/finalpage/2025-07-05/1224085489.PDF',
        'sha256':'af3c4f592fa7573eeb279a2f8aa32d554d4f04731f729c9b7b703e77b295cea3',
    },
}
ZHONGYAN_LIMIT = 'sz.003001 2025-07-07 原实施/后续调整公告已分开原样保存；调整公告在登记日后公开，实发现金/转增与除权摊薄比例不同，另有循环进位零碎股规则；该行动的时钟、参考价和零碎结算尚未核验，停止账本。'

JIANGBOLONG_ROWS = {
    'a9f1879bf2de1a86f770aea5e0d24b042266977719c20c16f7994c37015eccad': 'annual_2025_source_component',
    '28dc132a3e194a4179565635c35ce3527033a85a1514c6b888423a4887f07c8a': 'first_interim_2026_source_component',
}
JIANGBOLONG_LIMIT = 'sz.301308 2026-06-02 年度与第一次中期分红原行已分别保存；SDK现金合计每股0.990744与正式公告0.9907442不同，公告规定不足一分钱舍尾，与当前账本四舍五入不同；精度、合并及现金尾数结算尚未支持，停止账本。'


def collection_revision(row):
    if row.get('code') == 'sz.301308' and row.get('dividOperateDate') == '2026-06-02':
        digest = sha256(json.dumps(row,ensure_ascii=False,sort_keys=True,separators=(',',':')).encode()).hexdigest()
        label = JIANGBOLONG_ROWS.get(digest)
        if label is None:
            raise ValueError('江波龙2026-06-02出现未核验的完整源行，未绕过条款冲突')
        reference = {'label':label,'announcement_date':'2026-05-26','announcement':'2026-065',
                     'document':'docs/research/jiangbolong-2026-joint-dividend.pdf',
                     'source_url':'https://static.cninfo.com.cn/finalpage/2026-05-26/1225331662.PDF',
                     'sha256':'25ec59eab647f69b51f34936fef2a23f937f5d86c081f4b9d25e983a4f8c8a40'}
        document = ROOT / reference['document']
        if not document.is_file() or sha256(document.read_bytes()).hexdigest() != reference['sha256']:
            raise ValueError('江波龙共同实施公告原件缺失或摘要变化')
        return {**reference,'source_row_sha256':digest,'use':'collection_only','backtest_limit':JIANGBOLONG_LIMIT}
    if row.get('code') != 'sz.003001' or row.get('dividOperateDate') != '2025-07-07':
        return None
    digest = sha256(json.dumps(row,ensure_ascii=False,sort_keys=True,separators=(',',':')).encode()).hexdigest()
    reference = ZHONGYAN_REVISIONS.get(digest)
    if reference is None:
        raise ValueError('中岩大地2025-07-07出现未核验的完整源版本，未绕过条款冲突')
    document = ROOT / reference['document']
    if not document.is_file() or sha256(document.read_bytes()).hexdigest() != reference['sha256']:
        raise ValueError('中岩大地原实施/调整公告原件缺失或摘要变化')
    return {**reference,'source_row_sha256':digest,'use':'collection_only','backtest_limit':ZHONGYAN_LIMIT}
