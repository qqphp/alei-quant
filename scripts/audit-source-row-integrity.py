"""固定版本全部发布原行（SDK及明确公告补充）的字段/摘要审计；只读。"""
import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
from pathlib import Path
import time

from stockapp import db
from stockapp.catalog import BY_METHOD

parser = argparse.ArgumentParser()
parser.add_argument('--version', type=int)
args = parser.parse_args()
started = time.monotonic()
datasets, methods = Counter(), Counter()
ordered = hashlib.sha256()
count = 0
with db.connect() as conn:
    conn.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY')
    maximum = conn.execute("SELECT max(id) AS version FROM data_batches WHERE status='published'").fetchone()['version']
    version = args.version if args.version is not None else maximum
    assert 0 < version <= maximum
    expected = {row['dataset']: row['n'] for row in conn.execute("SELECT r.dataset,count(*) AS n FROM source_records r JOIN data_batches b ON b.id=r.batch_id WHERE b.status='published' AND r.batch_id<=%s GROUP BY r.dataset", (version,))}
    with conn.cursor(name='all_source_row_integrity') as cursor:
        cursor.itersize = 10000
        cursor.execute("SELECT r.id,r.dataset,r.record_key,r.payload,r.content_hash,r.batch_id,b.fields,b.method FROM source_records r JOIN data_batches b ON b.id=r.batch_id WHERE b.status='published' AND r.batch_id<=%s ORDER BY r.id", (version,))
        for row in cursor:
            fields, payload = row['fields'], row['payload']
            assert len(fields) == len(set(fields)) and set(fields) == set(payload), ('source_field_shape', row['id'], row['batch_id'])
            received = {field: payload[field] for field in fields}
            digest = hashlib.sha256(db.dumps(received).encode('utf-8')).hexdigest()
            assert digest == row['content_hash'], ('source_content_hash', row['id'], row['batch_id'])
            ordered.update(db.dumps([row['id'], row['batch_id'], row['dataset'], row['record_key'], digest]).encode('utf-8') + b'\n')
            datasets[row['dataset']] += 1
            methods[row['method']] += 1
            count += 1
            if count % 100000 == 0:
                print(db.dumps({'data_version': version, 'rows_checked': count, 'elapsed_seconds': round(time.monotonic() - started, 2)}), flush=True)
    assert dict(datasets) == expected
evidence = {'checked_at': datetime.now(timezone.utc).isoformat(), 'data_version': version,
            'source_rows': count, 'by_dataset': dict(datasets), 'by_method': dict(methods),
            'all_original_field_shapes_equal': True, 'all_original_field_order_hashes_equal': True,
            'independent_dataset_counts_equal': True, 'ordered_source_identity_sha256': ordered.hexdigest(),
            'elapsed_seconds': time.monotonic() - started, 'main_database_writes': 0, 'sdk_calls': 0,
            'sdk_methods_with_records': sorted(method for method in methods if method in BY_METHOD),
            'non_sdk_published_methods': {method: number for method, number in methods.items() if method not in BY_METHOD},
            'scope': '固定版本、同一只读事务内全部发布源修订，包括SDK及明确标记的正式公告补充。每行按批次原字段顺序重建并重算SHA256，精确保留字符串/空值/税后说明等；不证明源值真伪、首次发布历史、全部经济行情或权益完整。未发布冲突原证据不属于发布源记录，最终恢复另核对批次。'}
Path('docs/verification-all-source-row-integrity.json').write_text(db.dumps(evidence) + '\n', encoding='utf-8')
print(db.dumps({'verified': True, 'data_version': version, 'source_rows': count, 'datasets': len(datasets)}), flush=True)
