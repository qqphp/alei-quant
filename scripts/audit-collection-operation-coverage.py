"""Checkpoint progress must correspond to published receipts for exact requests."""
from collections import Counter
import argparse
from datetime import datetime, timezone
from hashlib import sha256
import json
from pathlib import Path
from stockapp import db, provider
from stockapp.catalog import validate_operation

parser = argparse.ArgumentParser()
parser.add_argument('--version', type=int)
arguments = parser.parse_args()


def key(operation):
    return operation['method'] + '|' + json.dumps(operation['arguments'], sort_keys=True, separators=(',', ':'))


job_ids = ['3a12e654-ac88-4be4-800c-d784e07551b5', '992e8963-2eb8-4dbd-86f2-8f9c2874e1eb']
# Read immutable operation definitions first; classifications must still produce the saved digest.
definitions = {}
for jid in job_ids:
    job = db.one('SELECT config,checkpoint FROM jobs WHERE id=%s', (jid,))
    operations = [validate_operation(o) for o in (job['config'].get('operations') or provider.bundle(job['config']))]
    digest = sha256(db.dumps(operations).encode()).hexdigest()
    assert digest == job['checkpoint']['operations_digest'], jid
    assert len(operations) == len(job['config']['codes'])*6+2
    assert len({key(o) for o in operations}) == len(operations)
    definitions[jid] = (operations, digest)

with db.connect() as conn:
    conn.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY')
    version = conn.execute("SELECT max(id) AS version FROM data_batches WHERE status='published'").fetchone()['version']
    if arguments.version is not None:
        assert 0 < arguments.version <= version
        version = arguments.version
    checks = []
    for jid in job_ids:
        job = conn.execute('SELECT id,status,progress,total,error,checkpoint FROM jobs WHERE id=%s', (jid,)).fetchone()
        operations, digest = definitions[jid]
        assert job['checkpoint']['operations_digest'] == digest
        cursor = job['checkpoint']['next_operation']
        assert job['progress'] == cursor and job['total'] == len(operations)
        batches = conn.execute('SELECT id,method,arguments,status,row_count,changed_count,coverage FROM data_batches WHERE job_id=%s AND id<=%s ORDER BY id', (jid,version)).fetchall()
        published = {}
        failed = []
        allowed = {key(o) for o in operations}
        for batch in batches:
            identity = key(batch)
            assert identity in allowed, (jid, batch['id'])
            if batch['status'] == 'published':
                published.setdefault(identity, []).append(batch)
            elif batch['status'] == 'failed':
                failed.append(batch['id'])
        missing = [i for i in range(cursor) if key(operations[i]) not in published]
        assert not missing, (jid, missing)
        processed, remaining = Counter(), Counter()
        receipts = []
        for i, operation in enumerate(operations):
            if i >= cursor:
                remaining[operation['method']] += 1
                continue
            processed[operation['method']] += 1
            batch = published[key(operation)][-1]
            receipts.append({'operation': i, 'batch': batch['id'], 'method': batch['method'],
                             'arguments': batch['arguments'], 'received': batch['row_count'],
                             'changed': batch['changed_count'],
                             'non_price_markers': len(batch['coverage'].get('non_price_markers', [])),
                             'collection_only_revisions': len(batch['coverage'].get('collection_only_revisions', []))})
        checks.append({'job': job, 'operation_count': len(operations), 'operations_digest': digest,
                       'processed_by_method': dict(processed), 'remaining_by_method': dict(remaining),
                       'all_checkpoint_requests_have_published_receipts': True,
                       'failed_attempt_batches_retained': failed, 'receipts': receipts})
proof = {'checked_at': datetime.now(timezone.utc).isoformat(), 'data_version': version, 'jobs': checks,
         'scope': '两任务的冻结代码范围生成完整原操作摘要，每个已提交检查点位置对应同任务同方法/完整参数的发布批次，失败尝试保留。请求完成不证明返回的经济行情/权益完整；空结果、代码变更标记及仅采集事件不变成可交易记录。来源回溯成员行情存在性与公司行动边界仍须另审计。只读，无SDK。'}
Path('docs/verification-collection-operation-coverage.json').write_text(db.dumps(proof), encoding='utf-8')
print(db.dumps({'version': version, 'jobs': [{k:c[k] for k in ('job','processed_by_method','remaining_by_method','all_checkpoint_requests_have_published_receipts')} for c in checks]}))
