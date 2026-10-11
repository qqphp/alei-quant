"""Verify post-10:06 delivery against the independently restored backup."""
import hashlib
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import time
from urllib.error import URLError
from urllib.request import urlopen
from uuid import uuid4

from fastapi.encoders import jsonable_encoder
from stockapp import db, plugins
from stockapp.config import ROOT, settings

target = Path(sys.argv[1]).resolve()
clone = json.loads((target / 'restore-verification.json').read_text())['check_database']
cfg = settings()
assert cfg.db_name == 'stock_alei' and clone.startswith('stock_alei_restore_')
proof = {name: json.loads((target / 'docs' / name).read_text(encoding='utf-8')) for name in (
    'verification-current-parameter-research.json', 'verification-current-model-lifecycle.json',
    'verification-bytecode-package-installation.json')}
research = proof['verification-current-parameter-research.json']
lifecycle = proof['verification-current-model-lifecycle.json']
cached = proof['verification-bytecode-package-installation.json']
ids = [row['run_id'] for row in research['runs']] + [
    lifecycle['current_run'], lifecycle['replay_after_disable'],
    cached['current_run'], cached['replay_after_disable']]
original = {rid: db.one('SELECT manifest,result FROM backtest_runs WHERE id=%s', (rid,)) for rid in ids}
experiment = db.one('SELECT * FROM experiments WHERE id=%s', (research['experiment_id'],))
models = db.rows('SELECT id,version,digest,manifest,enabled FROM strategies ORDER BY id,version,digest')
files = {}
for name in ('backend', 'scripts', 'frontend/src', 'frontend/dist'):
    for path in (target / name).rglob('*'):
        if path.is_file() and '__pycache__' not in path.parts and path.suffix.lower() != '.pyc':
            relative = path.relative_to(target).as_posix()
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
            assert hashlib.sha256((ROOT / relative).read_bytes()).hexdigest() == digest, relative
            files[relative] = digest
for name in ('README.md', 'docs/strategy-contract.md', 'frontend/package.json', 'frontend/package-lock.json'):
    digest = hashlib.sha256((target / name).read_bytes()).hexdigest()
    assert hashlib.sha256((ROOT / name).read_bytes()).hexdigest() == digest
    files[name] = digest
assert not (target / '.env').exists()

os.environ['STOCK_DB_NAME'] = clone
settings.cache_clear()
assert db.rows('SELECT id,version,digest,manifest,enabled FROM strategies ORDER BY id,version,digest') == models
restored_experiment = db.one('SELECT * FROM experiments WHERE id=%s', (research['experiment_id'],))
expected_experiment = json.loads(db.dumps(experiment).replace(str(ROOT).replace('\\', '\\\\'), str(target).replace('\\', '\\\\')))
assert json.loads(db.dumps(restored_experiment)) == expected_experiment
for model in db.rows('SELECT source_path,digest FROM strategies'):
    assert Path(model['source_path']).is_relative_to(target)
    assert plugins.digest_directory(Path(model['source_path'])) == model['digest']

replays = []
for rid in ids:
    row = db.one('SELECT manifest,result FROM backtest_runs WHERE id=%s', (rid,))
    expected_manifest = json.loads(db.dumps(original[rid]['manifest']).replace(
        str(ROOT).replace('\\', '\\\\'), str(target).replace('\\', '\\\\')))
    assert row['manifest'] == expected_manifest and row['result'] == original[rid]['result'], rid
for rid in (next(row['run_id'] for row in research['runs'] if row['stage'] == 'out_of_sample'),
            lifecycle['current_run'], cached['current_run']):
    frozen = db.one('SELECT manifest FROM backtest_runs WHERE id=%s', (rid,))['manifest']
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
    result = db.one('SELECT r.result,j.status,j.error FROM backtest_runs r JOIN jobs j ON j.id=r.id WHERE r.id=%s', (replay_id,))
    assert result['status'] == 'completed' and result['result'] == original[rid]['result'], (rid, result['error'])
    replays.append({'original': rid, 'replay': replay_id, 'full_result_equal': True})
    print(db.dumps(replays[-1]), flush=True)

with socket.socket() as available:
    available.bind(('127.0.0.1', 0))
    port = available.getsockname()[1]
api_environment = dict(environment, PYTHONPATH=str(target / 'backend'))
def canonical(value):
    return json.loads(json.dumps(jsonable_encoder(value)))
with (target / 'restore-latest-delivery-api.log').open('w', encoding='utf-8') as log:
    process = subprocess.Popen([sys.executable, '-m', 'uvicorn', 'stockapp.api:app', '--host', '127.0.0.1', '--port', str(port)],
                               cwd=target, env=api_environment, stdout=log, stderr=log, creationflags=subprocess.CREATE_NO_WINDOW)
    def read(path):
        with urlopen(f'http://127.0.0.1:{port}/api' + path, timeout=30) as response:
            return json.load(response)
    try:
        for _ in range(100):
            assert process.poll() is None
            try:
                health = read('/health')
                assert health['database'] == clone and not health['worker']
                break
            except URLError:
                time.sleep(0.1)
        else:
            raise RuntimeError('Restored API did not become ready')
        stored_runs = db.rows("SELECT r.id,r.manifest->'config' AS config,r.manifest->>'data_version' AS data_version,r.result->'metrics' AS metrics,j.status,j.created_at FROM backtest_runs r JOIN jobs j ON j.id=r.id ORDER BY j.created_at DESC,r.id DESC")
        first = read('/backtests?paged=true&size=20')
        actual_runs = first['data']
        for page in range(2, (first['total'] + 19) // 20 + 1):
            actual_runs += read(f'/backtests?paged=true&size=20&page={page}')['data']
        assert actual_runs == canonical(stored_runs)
        history = db.rows("SELECT id,kind,status,progress,total,error,created_at,started_at,finished_at,heartbeat_at,cancel_requested FROM jobs WHERE status NOT IN ('queued','running','waiting') ORDER BY created_at DESC,id DESC")
        active = db.rows("SELECT id,kind,status,progress,total,error,created_at,started_at,finished_at,heartbeat_at,cancel_requested FROM jobs WHERE status IN ('queued','running','waiting') ORDER BY created_at DESC,id DESC")
        actual_history = []
        for page in range(1, (len(history) + 19) // 20 + 1):
            response = read(f'/jobs?paged=true&size=20&page={page}')
            assert response['total'] == len(history) and response['active_count'] == len(active)
            assert response['data'][:len(active)] == canonical(active)
            actual_history += response['data'][len(active):]
        assert actual_history == canonical(history)
        log_job = '1c2c9fc2-0ead-485b-9088-26ec7c3062bf'
        logs = db.rows('SELECT level,message,created_at FROM job_logs WHERE job_id=%s ORDER BY id DESC', (log_job,))
        actual_logs = []
        for page in range(1, (len(logs) + 99) // 100 + 1):
            response = read(f'/jobs/{log_job}?log_size=100&log_page={page}')
            assert response['logs_total'] == len(logs)
            actual_logs += response['logs']
        assert actual_logs == canonical(logs)
        for rid in ids:
            report = read(f'/backtests/{rid}')
            assert report['result'] == original[rid]['result']
    finally:
        process.terminate()
        process.wait(timeout=10)

evidence = {'backup': str(target), 'clone': clone, 'files_sha256': files, 'model_states_equal': len(models),
            'stored_reports_equal': len(ids), 'research_frozen_and_selection_equal': True, 'replays': replays,
            'http_runs_all_fields_equal': len(stored_runs), 'http_terminal_jobs_all_fields_equal': len(history),
            'http_active_jobs_per_page_equal': len(active), 'http_logs_all_fields_equal': len(logs),
            'restored_api_stopped': process.poll() is not None,
            'scope': '独立恢复库；模型状态、九份最新报告及参数研究完整记录相等，三份归档全结果重算，全部运行/任务/日志分页HTTP核对；没有采集调用或主库写入。恢复工具标记的价格修订仅在副本，固定版本仍读取原值。'}
(target / 'restore-latest-delivery-verification.json').write_text(db.dumps(evidence), encoding='utf-8')
(ROOT / 'docs/verification-latest-delivery-restore.json').write_text(db.dumps(evidence), encoding='utf-8')
print(db.dumps({'verified': True, 'clone': clone, 'replays': len(replays), 'files': len(files)}))
