"""完整来源池按固定版本和保守日线时钟提供；不声称首次发布真值。"""
from bisect import bisect_right
from hashlib import sha256
import sys

from . import db
from .market import ready_at


NOTE = "指定日完整来源回溯成员和 tradeStatus；17:30 后才可用于信号。不是历史首次发布原版，实际新代码可在正式启用前被来源回溯返回；成员/状态不能单独证明当时可交易。名称、当前类型与 ST 不传入，行情及资产规则仍独立校验；候选交易池仍为运行配置 codes。"


class HistoricalUniverse:
    def __init__(self, days, version):
        self.days = sorted(set(days))
        metadata = db.rows("SELECT DISTINCT ON(s.day) s.day::text,s.batch_id FROM universe_snapshots s "
                           "JOIN data_batches b ON b.id=s.batch_id WHERE b.status='published' "
                           "AND s.day BETWEEN %s AND %s AND s.batch_id<=%s ORDER BY s.day,s.batch_id DESC",
                           (self.days[0],self.days[-1],version))
        snapshots = {row['day']:row for row in metadata}
        missing = [day for day in self.days if day not in snapshots]
        if missing:
            raise ValueError(f"模型历史证券池缺少 {len(missing)} 个完整快照，例如 {missing[0]}；未借用当前或相邻日名单")
        self.clocks = [ready_at(day,'d') for day in self.days]
        self.loaded = {}
        for offset in range(0,len(self.days),31):
            batches = [snapshots[day]['batch_id'] for day in self.days[offset:offset+31]]
            # 成员数组也分块读取，避免先在内存中展开整个区间数百万个重复字符串。
            group = db.rows("SELECT day::text,batch_id,codes FROM universe_snapshots WHERE batch_id=ANY(%s)", (batches,))
            if len(group) != len(batches):
                raise ValueError("历史池完整快照成员集合缺失，未提供部分输入")
            states = db.rows("SELECT s.batch_id,count(*) AS count,"
                             "count(*) FILTER(WHERE p.payload->>'code' IS DISTINCT FROM member.code OR "
                             "(p.payload->>'tradeStatus' IS DISTINCT FROM '0' AND p.payload->>'tradeStatus' IS DISTINCT FROM '1')) AS invalid,"
                             "string_agg(p.payload->>'tradeStatus','' ORDER BY member.code) AS states "
                             "FROM universe_snapshots s CROSS JOIN LATERAL unnest(s.codes) member(code) "
                             "LEFT JOIN LATERAL (SELECT r.payload FROM source_records r JOIN data_batches b ON b.id=r.batch_id "
                             "WHERE r.dataset='universe' AND r.record_key=s.day::text||'|'||member.code "
                             "AND r.batch_id<=s.batch_id AND b.status='published' ORDER BY r.batch_id DESC LIMIT 1) p ON true "
                             "WHERE s.batch_id=ANY(%s) GROUP BY s.batch_id", ([row['batch_id'] for row in group],))
            by_batch = {row['batch_id']:row for row in states}
            for source in group:
                codes = source['codes']
                state = by_batch.get(source['batch_id'])
                if not codes or codes != sorted(set(codes)) or not state or state['invalid'] or state['count'] != len(codes) or len(state['states'] or '') != len(codes):
                    raise ValueError(f"{source['day']} 历史池原始成员/状态不完整或冲突，未提供部分输入")
                codes = tuple(sys.intern(code) for code in codes)
                statuses = state['states']
                digest = sha256((source['day']+'\n'+'\n'.join(code+'|'+status for code,status in zip(codes,statuses))).encode()).hexdigest()
                reference = {'day':source['day'],'source_batch':source['batch_id'],'data_version':version,
                             'available_at':ready_at(source['day'],'d').isoformat(),'members':len(codes),
                             'digest':digest,'digest_algorithm':'SHA256 UTF8: day + newline + sorted code|tradeStatus joined by newline; no trailing newline',
                             'note':NOTE}
                self.loaded[source['day']] = (codes,statuses,reference)

    def visible(self, at):
        if at.tzinfo is None or at.utcoffset() is None:
            raise ValueError("历史池信号时点须含时区")
        index = bisect_right(self.clocks,at)-1
        if index < 0:
            raise ValueError("信号之前没有已就绪的完整历史池，未使用未来快照")
        codes,statuses,reference = self.loaded[self.days[index]]
        # 每次构造独立副本；策略修改自己的输入不会影响下一回调或报告依据。
        return {**reference,'data':[{'code':code,'tradeStatus':status} for code,status in zip(codes,statuses)]}
