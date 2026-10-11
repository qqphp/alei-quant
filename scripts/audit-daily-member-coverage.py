"""按整段真实历史成员核对日线存在性；运行中也明确披露剩余缺口。"""
from collections import Counter, defaultdict
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
from stockapp import db, universe

parser = argparse.ArgumentParser()
parser.add_argument('--version', type=int)
parser.add_argument('--output', type=Path, default=Path('docs/verification-daily-member-coverage.json'))
arguments = parser.parse_args()

with db.connect() as conn:
    conn.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY')
    version = conn.execute("SELECT max(id) AS version FROM data_batches WHERE status='published'").fetchone()['version']
    if arguments.version is not None:
        assert 0 < arguments.version <= version
        version = arguments.version
    basic = conn.execute("SELECT DISTINCT ON(r.code) r.code,r.payload->>'type' AS type FROM source_records r JOIN data_batches b ON b.id=r.batch_id WHERE r.dataset='instruments' AND b.status='published' AND r.batch_id<=%s ORDER BY r.code,r.batch_id DESC", (version,)).fetchall()
    types = {r['code']:r['type'] for r in basic}
    snapshots = conn.execute("SELECT DISTINCT ON(s.day) s.day::text,s.codes FROM universe_snapshots s JOIN data_batches b ON b.id=s.batch_id WHERE s.day BETWEEN '2023-01-01' AND '2026-10-09' AND s.batch_id<=%s AND b.status='published' ORDER BY s.day,s.batch_id DESC", (version,)).fetchall()
    assert len(snapshots) == 910
    unknown = {code for snapshot in snapshots for code in snapshot['codes'] if not types.get(code)}
    references = {code: universe.CLASSIFICATION_REFERENCES[code] for code in unknown if code in universe.CLASSIFICATION_REFERENCES}
    assert unknown == set(references), '未识别的成员不能静默排除'
    types.update({code:reference['type'] for code, reference in references.items()})
    receipts = conn.execute("SELECT id,arguments,row_count FROM data_batches WHERE status='published' AND id<=%s AND job_id=ANY(%s::uuid[]) AND method='query_history_k_data_plus'", (version,['3a12e654-ac88-4be4-800c-d784e07551b5','992e8963-2eb8-4dbd-86f2-8f9c2874e1eb'])).fetchall()
    full_requests = defaultdict(list)
    for receipt in receipts:
        args = receipt['arguments']
        assert args['frequency'] == 'd' and args['adjustflag'] == '3'
        assert args['start_date'] == '2023-01-01' and args['end_date'] == '2026-10-09'
        full_requests[args['code']].append({'batch':receipt['id'], 'received':receipt['row_count']})
    extents = {row['code']:row for row in conn.execute("SELECT r.code,min(r.period) AS first,max(r.period) AS last FROM source_records r JOIN data_batches b ON b.id=r.batch_id WHERE r.dataset='bars:d' AND r.period BETWEEN '2023-01-01' AND '2026-10-09' AND r.batch_id<=%s AND b.status='published' GROUP BY r.code", (version,)).fetchall()}
    price_cursor = conn.cursor(name='source_daily_presence')
    price_cursor.itersize = 10000
    price_cursor.execute("SELECT DISTINCT r.period,r.code FROM source_records r JOIN data_batches b ON b.id=r.batch_id WHERE r.dataset='bars:d' AND r.period BETWEEN '2023-01-01' AND '2026-10-09' AND r.batch_id<=%s AND b.status='published' ORDER BY r.period,r.code", (version,))
    prices = iter(price_cursor)
    next_price = next(prices, None)
    marker_rows = conn.execute("SELECT id,marker FROM data_batches CROSS JOIN LATERAL jsonb_array_elements(COALESCE(coverage->'non_price_markers','[]'::jsonb)) marker WHERE status='published' AND id<=%s", (version,)).fetchall()
    markers = {(row['marker']['raw']['code'],row['marker']['raw']['date']) for row in marker_rows}
    days, missing, categories = [], Counter(), Counter()
    per_code, examples = defaultdict(Counter), defaultdict(list)
    for snapshot in snapshots:
        codes = sorted(c for c in snapshot['codes'] if types.get(c) == '1')
        day_prices = set()
        while next_price is not None and next_price['period'] <= snapshot['day']:
            if next_price['period'] == snapshot['day']:
                day_prices.add(next_price['code'])
            next_price = next(prices, None)
        absent = [{'code':code} for code in sorted(set(codes) - day_prices)]
        missing.update(r['code'] for r in absent)
        day_categories = Counter()
        for row in absent:
            code, day = row['code'], snapshot['day']
            extent = extents.get(code)
            if (code, day) in markers:
                category = 'published_non_price_marker'
            elif code not in full_requests:
                category = 'full_range_request_not_published'
            elif not extent:
                category = 'published_range_without_any_price'
            elif day < extent['first']:
                category = 'before_first_available_price'
            elif day > extent['last']:
                category = 'after_last_available_price'
            else:
                category = 'gap_inside_available_price_span'
            day_categories[category] += 1
            per_code[code][category] += 1
            if len(examples[category]) < 5:
                examples[category].append({'code':code,'day':day,'price_extent':extent,'full_range_receipts':full_requests.get(code,[])})
        categories.update(day_categories)
        days.append({'day':snapshot['day'], 'expected_members':len(codes), 'present':len(codes)-len(absent), 'missing':len(absent), 'missing_categories':dict(day_categories)})
    price_cursor.close()
    jobs = conn.execute("SELECT id,status,progress,total,error,heartbeat_at FROM jobs WHERE id=ANY(%s::uuid[])", (['3a12e654-ac88-4be4-800c-d784e07551b5','992e8963-2eb8-4dbd-86f2-8f9c2874e1eb'],)).fetchall()
proof = {'checked_at':datetime.now(timezone.utc).isoformat(), 'version':version, 'days':days, 'classification_sources':references, 'expected_member_dates':sum(r['expected_members'] for r in days), 'present_member_dates':sum(r['present'] for r in days), 'missing_member_dates':sum(missing.values()), 'missing_codes':dict(missing), 'jobs':jobs, 'all_daily_members_present':not missing, 'main_database_writes':0, 'sdk_calls':0, 'scope':'Same read-only transaction, all 910 source-calendar member dates, current fixed archive type=1 plus explicit official class reference. Published raw daily source-record presence at the fixed version, independent of the latest cache pointer; does not prove OHLC correctness, company action completeness, or historical first-release truth. Missing member dates may expose provider discrepancies, not just pending collection.'}
assert sum(categories.values()) == sum(missing.values())
assert all(sum(per_code[code].values()) == count for code,count in missing.items())
proof.update({'missing_categories':dict(categories), 'missing_categories_by_code':{code:dict(counts) for code,counts in per_code.items()}, 'category_examples':dict(examples), 'category_scope':'互斥事实分类：精确已发布非价格标记、原主/额外任务整段请求尚未发布、已发布但本区间无任何价格、已有价格之前/之后/中间。先分类非价格标记，再按整段发布回执及实际价格范围；不据此推断上市、退市、当时可交易或来源缺失原因。无整段回执不代表完全无局部行情；零返回也不证明无公司行动。'})
arguments.output.write_text(db.dumps(proof)+'\n','utf-8')
print(db.dumps({k:proof[k] for k in ('version','expected_member_dates','present_member_dates','missing_member_dates','missing_categories','all_daily_members_present','jobs')}))
