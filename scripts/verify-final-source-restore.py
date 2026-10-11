"""在最终回填完成后，核对独立恢复库的全部固定版本源值及最新账本报告。

前置：三个覆盖审计及全量源行摘要审计使用同一 --version；backup、restore-check 已完成。
仅在恢复库写入标明的重算任务，不调用 SDK，不改主库。
"""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
from uuid import uuid4

from stockapp import db, plugins
from stockapp.config import ROOT, settings


target = Path(sys.argv[1]).resolve()
assert target.is_relative_to(ROOT / 'runtime' / 'backups')
restoration = json.loads((target / 'restore-verification.json').read_text(encoding='utf-8'))
assert restoration['status'] == 'verified'
backup_metadata = json.loads((target / 'backup.json').read_text(encoding='utf-8'))
assert backup_metadata['status'] == 'completed' and backup_metadata.get('files_sha256'), '最终备份需包含复制时源/副本相等的文件摘要清单'
for relative, digest in backup_metadata['files_sha256'].items():
    path = (target / relative).resolve()
    assert path.is_relative_to(target)
    assert hashlib.sha256(path.read_bytes()).hexdigest() == digest, relative
clone = restoration['check_database']
cfg = settings()
assert cfg.db_name == 'stock_alei' and clone.startswith('stock_alei_restore_')
audits = {}
for name in ('collection-operation-coverage', 'daily-member-coverage', 'action-source-integrity', 'all-source-row-integrity'):
    filename = 'verification-' + name + '.json'
    raw = (target / 'docs' / filename).read_bytes()
    assert raw == (ROOT / 'docs' / filename).read_bytes(), filename
    audits[name] = json.loads(raw)
version = audits['collection-operation-coverage']['data_version']
assert version == audits['daily-member-coverage']['version'] == audits['action-source-integrity']['data_version']
assert version == audits['all-source-row-integrity']['data_version']
assert audits['all-source-row-integrity']['all_original_field_shapes_equal']
assert audits['all-source-row-integrity']['all_original_field_order_hashes_equal']
for job in audits['collection-operation-coverage']['jobs']:
    assert job['job']['status'] == 'completed'
    assert job['job']['progress'] == job['job']['total'] == job['operation_count']
    assert not job['remaining_by_method'] and job['all_checkpoint_requests_have_published_receipts']
assert audits['action-source-integrity']['all_persisted_revision_field_order_hashes_equal']
assert audits['action-source-integrity']['all_source_code_period_equal']
members = audits['daily-member-coverage']
assert len(members['days']) == 910
assert members['present_member_dates'] + members['missing_member_dates'] == members['expected_member_dates']
assert sum(members['missing_categories'].values()) == members['missing_member_dates']


def source_fingerprints():
    queries = {
        'published_source_records': "SELECT r.* FROM source_records r JOIN data_batches b ON b.id=r.batch_id WHERE b.status='published' AND r.batch_id<=%s",
        'all_data_batches': 'SELECT b.* FROM data_batches b WHERE b.id<=%s',
        'universe_snapshots': 'SELECT s.* FROM universe_snapshots s WHERE s.batch_id<=%s',
    }
    result = {}
    with db.connect() as conn:
        conn.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY')
        for name, query in queries.items():
            order = 'batch_id' if name == 'universe_snapshots' else 'id'
            digest, count = hashlib.sha256(), 0
            with conn.cursor(name='restore_' + name) as cursor:
                cursor.itersize = 10000
                cursor.execute("SELECT encode(sha256(convert_to(to_jsonb(item)::text,'UTF8')),'hex') AS digest FROM (" + query + ') item ORDER BY item.' + order, (version,))
                for row in cursor:
                    digest.update(row['digest'].encode('ascii') + b'\n')
                    count += 1
                    if count % 250000 == 0:
                        print(db.dumps({'database': settings().db_name, 'table': name, 'rows_checked': count}), flush=True)
            result[name] = {'rows': count, 'sha256': digest.hexdigest()}
            print(db.dumps({'database': settings().db_name, 'table': name, **result[name]}), flush=True)
    return result


main_fingerprints = source_fingerprints()
assert main_fingerprints['published_source_records']['rows'] == audits['all-source-row-integrity']['source_rows']
run_ids = ['7e1fe3e4-7295-4101-9f96-18da7a1b0226',
           '87011ede-7dbc-43e0-9e73-0f1e3da83377', '61cfb614-85c5-4d08-b50a-3aa1ab84e44d']
reports = {rid: db.one('SELECT r.manifest,r.result,j.status FROM backtest_runs r JOIN jobs j ON j.id=r.id WHERE r.id=%s', (rid,)) for rid in run_ids}
assert all(report and report['status'] == 'completed' for report in reports.values())
document_hashes = {}
generated_after_snapshot = []
for path in (target / 'docs').rglob('*'):
    if path.is_file():
        relative = path.relative_to(target)
        snapshot_hash = hashlib.sha256(path.read_bytes()).hexdigest()
        assert backup_metadata['files_sha256'][relative.as_posix()] == snapshot_hash
        current_hash = hashlib.sha256((ROOT / relative).read_bytes()).hexdigest()
        if current_hash != snapshot_hash:
            assert relative.as_posix() == 'docs/verification-latest-delivery-restore.json', str(relative)
            latest = json.loads((ROOT / relative).read_text(encoding='utf-8'))
            assert latest['backup'] == str(target) and latest['clone'] == clone
            assert len(latest['replays']) == 3 and all(r['full_result_equal'] for r in latest['replays'])
            generated_after_snapshot.append({'path': relative.as_posix(), 'snapshot_sha256': snapshot_hash,
                                             'new_verification_sha256': current_hash, 'actual_backup_and_clone_verified': True})
        document_hashes[relative.as_posix()] = snapshot_hash
assert not (target / '.env').exists()

os.environ['STOCK_DB_NAME'] = clone
settings.cache_clear()
try:
    restored_fingerprints = source_fingerprints()
    assert restored_fingerprints == main_fingerprints
    replays = []
    for rid, original in reports.items():
        stored = db.one('SELECT manifest,result FROM backtest_runs WHERE id=%s', (rid,))
        expected = json.loads(db.dumps(original['manifest']).replace(
            str(ROOT).replace('\\', '\\\\'), str(target).replace('\\', '\\\\')))
        assert stored['manifest'] == expected and stored['result'] == original['result']
        frozen = stored['manifest']
        archive = Path(frozen['engine_source']['package'])
        assert archive.is_relative_to(target)
        assert plugins.digest_directory(archive / 'stockapp') == frozen['engine_source']['digest']
        replay_id = str(uuid4())
        with db.connect() as conn:
            conn.execute("INSERT INTO jobs(id,kind,config) VALUES(%s,'backtest',%s)", (replay_id, db.jb(frozen['config'])))
            conn.execute('INSERT INTO backtest_runs(id,manifest) VALUES(%s,%s)', (replay_id, db.jb(frozen)))
        environment = dict(os.environ, PYTHONPATH=str(archive), STOCK_PROJECT_ROOT=str(target),
                           STOCK_DB_HOST=cfg.db_host, STOCK_DB_PORT=str(cfg.db_port), STOCK_DB_NAME=clone,
                           STOCK_DB_USER=cfg.db_user, STOCK_DB_PASSWORD=cfg.db_password.get_secret_value(), PYTHONUTF8='1')
        subprocess.run([sys.executable, '-m', 'stockapp.runner', replay_id], cwd=target, env=environment,
                       timeout=300, check=True, creationflags=subprocess.CREATE_NO_WINDOW)
        replayed = db.one('SELECT r.result,j.status,j.error FROM backtest_runs r JOIN jobs j ON j.id=r.id WHERE r.id=%s', (replay_id,))
        assert replayed['status'] == 'completed' and replayed['result'] == original['result'], (rid, replayed['error'])
        replays.append({'original': rid, 'replay': replay_id, 'full_result_equal': True})
        print(db.dumps(replays[-1]), flush=True)
finally:
    os.environ['STOCK_DB_NAME'] = cfg.db_name
    settings.cache_clear()

evidence = {'backup': str(target), 'clone': clone, 'data_version': version,
            'all_fixed_source_values_equal': True, 'main_source_fingerprints': main_fingerprints,
            'restored_source_fingerprints': restored_fingerprints, 'documents_sha256': document_hashes,
            'backup_files_sha256': backup_metadata['files_sha256'],
            'generated_verifications_after_snapshot': generated_after_snapshot,
            'replays': replays, 'missing_member_dates': members['missing_member_dates'],
            'missing_categories': members['missing_categories'],
            'scope': '最终共同审计版本的全部已发布源记录、全部批次（含失败原证据）、历史池快照所有列逐行摘要相等；备份全部说明和正式原件逐文件相等；当前570日及迈瑞两个账本按恢复归档完整重算。来源空响应或已知限制保留；不宣称无缺口或首次发布真值。无SDK、主库写入。'}
text = db.dumps(evidence) + '\n'
(target / 'restore-final-source-verification.json').write_text(text, encoding='utf-8')
(ROOT / 'docs/verification-final-source-restore.json').write_text(text, encoding='utf-8')
print(db.dumps({'verified': True, 'data_version': version, 'replays': len(replays)}))
