"""Fixed-version factor/dividend provenance and conservative merge observations."""
from collections import Counter, defaultdict
import argparse
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path

from stockapp import db, corporate_actions, source_revisions

parser = argparse.ArgumentParser()
parser.add_argument('--version', type=int)
arguments = parser.parse_args()

with db.connect() as conn:
    conn.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY')
    version = conn.execute("SELECT max(id) AS version FROM data_batches WHERE status='published'").fetchone()['version']
    if arguments.version is not None:
        assert 0 < arguments.version <= version
        version = arguments.version
    rows = conn.execute("SELECT r.id,r.dataset,r.record_key,r.code,r.period,r.payload,r.content_hash,r.batch_id,b.fields,b.method "
                        "FROM source_records r JOIN data_batches b ON b.id=r.batch_id WHERE r.dataset IN ('factors','dividends') "
                        "AND b.status='published' AND b.id<=%s ORDER BY r.batch_id,r.id", (version,)).fetchall()
    totals, codes, variants, latest = Counter(), defaultdict(set), Counter(), {}
    ordered_hashes = sha256()
    collection_only = []
    for row in rows:
        payload, fields = row['payload'], row['fields']
        assert len(fields) == len(set(fields)) and set(fields) == set(payload), row['id']
        received = {field:payload[field] for field in fields}
        digest = sha256(db.dumps(received).encode()).hexdigest()
        assert digest == row['content_hash'], row['id']
        assert row['code'] == payload['code'], row['id']
        period = payload.get('dividOperateDate') or payload.get('date') or payload.get('dividPlanAnnounceDate')
        assert row['period'] == period, row['id']
        totals[row['dataset']] += 1
        codes[row['dataset']].add(row['code'])
        ordered_hashes.update(f"{row['id']}|{row['batch_id']}|{row['dataset']}|{row['record_key']}|{digest}\n".encode())
        latest[(row['dataset'],row['record_key'])] = row
        if '|source_text:' in row['record_key']:
            assert row['record_key'].endswith('|source_text:' + digest), row['id']
            variants[row['dataset']] += 1
        if row['dataset'] == 'dividends':
            revision = source_revisions.collection_revision(payload)
            if revision:
                collection_only.append({'source_id':row['id'],'batch':row['batch_id'],'code':row['code'],
                                        'period':row['period'],'label':revision['label'],
                                        'source_row_sha256':revision['source_row_sha256'], 'backtest_limit':revision['backtest_limit']})
    grouped = defaultdict(list)
    for (dataset,_), row in latest.items():
        if dataset == 'dividends':
            grouped[row['code']].append(row['payload'])
    merged, rejected = [], []
    for code, payloads in sorted(grouped.items()):
        try:
            actions = corporate_actions.merge_dividends(payloads)
        except ValueError as exc:
            rejected.append({'code':code,'source_rows':len(payloads),'error':str(exc)})
        else:
            merged.append({'code':code,'source_rows':len(payloads),'merged_actions':len(actions)})
    batches = conn.execute("SELECT method,count(*) AS published_batches,count(*) FILTER(WHERE row_count=0) AS empty_batches,"
                           "sum(row_count) AS received,sum(changed_count) AS changed FROM data_batches "
                           "WHERE status='published' AND id<=%s AND method IN ('query_adjust_factor','query_daily_adjust_factor','query_dividend_data') GROUP BY method ORDER BY method", (version,)).fetchall()
    jobs = conn.execute('SELECT id,status,progress,total,error FROM jobs WHERE id=ANY(%s::uuid[])',
                        (['3a12e654-ac88-4be4-800c-d784e07551b5','992e8963-2eb8-4dbd-86f2-8f9c2874e1eb'],)).fetchall()

proof = {'checked_at':datetime.now(timezone.utc).isoformat(),'data_version':version,
         'all_persisted_revision_field_order_hashes_equal':True,'all_source_code_period_equal':True,
         'revision_rows':dict(totals),'distinct_codes':{key:len(value) for key,value in codes.items()},
         'retained_source_text_variants':dict(variants),'ordered_source_identity_sha256':ordered_hashes.hexdigest(),
         'published_batches':batches,'collection_only_revisions':collection_only,
         'latest_dividend_codes':len(grouped),'merge_compatible_codes':merged,'merge_rejected_codes':rejected,
         'jobs':jobs,'sdk_calls':0,'main_database_writes':0,
         'scope':'同一固定版本只读事务；全部已发布因子/分红源修订按接收字段顺序重算内容摘要，代码/期间相等，保留税后文字变体及精确仅采集版本。批次接收数包含重复，空返回不证明无权益。最新自然键分红仅观察当前合并器的保守拒绝；合并相容不证明真实权益完整、参考除权现金、到账/零碎结算、历史披露时钟或相应证券已可回测。无SDK/主库写入；任务实际状态另列。'}
Path('docs/verification-action-source-integrity.json').write_text(db.dumps(proof)+'\n',encoding='utf-8')
print(db.dumps({key:proof[key] for key in ('data_version','revision_rows','distinct_codes','published_batches','latest_dividend_codes','merge_rejected_codes','jobs')}))
