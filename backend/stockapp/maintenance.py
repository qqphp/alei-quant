"""本机备份；数据库口令只传给子进程环境，不进入命令行或日志。"""
import argparse
from datetime import datetime, timezone
import json
import getpass
import hashlib
import os
from pathlib import Path
import shutil
import subprocess
import secrets
import sys
import re

import psycopg
from psycopg import sql

from . import db
from .config import ROOT, settings


def postgres_tool(name):
    available = shutil.which(name)
    if available:
        return available
    base = Path(os.environ.get("ProgramFiles", "C:/Program Files")) / "PostgreSQL"
    candidates = sorted(base.glob("*/bin/" + name + ".exe"), reverse=True)
    if not candidates:
        raise ValueError(f"找不到 {name}，请安装对应 PostgreSQL 客户端")
    return str(candidates[0])


def backup():
    cfg = settings()
    target = ROOT / "runtime" / "backups" / datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    target.mkdir(parents=True, exist_ok=False)
    environment = dict(os.environ, PGPASSWORD=cfg.db_password.get_secret_value())
    command = [postgres_tool("pg_dump"), "--host", cfg.db_host, "--port", str(cfg.db_port),
               "--username", cfg.db_user, "--dbname", cfg.db_name,
               "--format=custom", "--no-owner", "--no-privileges", "--file", str(target / "stock_alei.dump")]
    result = subprocess.run(command, env=environment, capture_output=True, text=True)
    if result.returncode:
        raise RuntimeError("pg_dump 失败，未把本次目录标记为完成；请检查数据库连接和权限")
    # pg_dump 完成后复制不可变版本；所有被引用的版本持续保留，包含快照内的新增版本。
    for name in ("strategies", "engines"):
        source = ROOT / "runtime" / name
        if source.exists():
            shutil.copytree(source, target / "runtime" / name, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    shutil.copytree(ROOT / "backend", target / "backend", ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    for name in ("frontend", "scripts", "docs", "strategies"):
        if (ROOT / name).exists():
            shutil.copytree(ROOT / name, target / name, ignore=shutil.ignore_patterns("node_modules", "__pycache__", "*.pyc"))
    for name in ("requirements.txt", "requirements.lock.txt", "README.md", ".env.example", ".gitignore"):
        if (ROOT / name).exists():
            shutil.copy2(ROOT / name, target / name)
    file_hashes = {}
    for path in target.rglob('*'):
        if not path.is_file() or path == target / 'stock_alei.dump':
            continue
        relative = path.relative_to(target)
        copied_hash = hashlib.sha256(path.read_bytes()).hexdigest()
        if hashlib.sha256((ROOT / relative).read_bytes()).hexdigest() != copied_hash:
            raise RuntimeError(f"备份复制期间源文件变化，未把本次目录标记为完成：{relative}")
        file_hashes[relative.as_posix()] = copied_hash
    (target / "backup.json").write_text(db.dumps({"status": "completed", "created_at": datetime.now(timezone.utc),
                                                  "original_root": str(ROOT), "database": cfg.db_name,
                                                  "postgres": db.one("SELECT version() AS version")["version"],
                                                  "data_version": db.latest_version(),
                                                  "files_sha256": file_hashes,
                                                  "note": "dump 为一致性快照，data_version 为完成后的观察水位；恢复后的库内版本才是权威"}), encoding="utf-8")
    return target


def restore_check(directory):
    target = Path(directory).resolve()
    metadata = json.loads((target / "backup.json").read_text(encoding="utf-8"))
    if metadata.get("status") != "completed" or not (target / "stock_alei.dump").is_file():
        raise ValueError("备份未完成或缺少数据库快照")
    cfg = settings()
    check_database = "stock_alei_restore_" + datetime.now().strftime("%Y%m%d_%H%M%S") + "_" + secrets.token_hex(3)
    if not re.fullmatch(r"stock_alei_restore_[a-z0-9_]+", check_database) or check_database == cfg.db_name:
        raise ValueError("恢复目标必须为独立验证库")
    admin_password = os.environ.get("STOCK_BOOTSTRAP_PASSWORD") or getpass.getpass("恢复验证：PostgreSQL 管理员密码: ")
    with psycopg.connect(host=cfg.db_host, port=cfg.db_port, dbname="postgres", user="postgres",
                         password=admin_password, autocommit=True) as admin:
        if admin.execute("SELECT 1 FROM pg_database WHERE datname=%s", (check_database,)).fetchone():
            raise ValueError("验证库已存在，未覆盖")
        admin.execute(sql.SQL("CREATE DATABASE {} OWNER {}").format(sql.Identifier(check_database), sql.Identifier(cfg.db_user)))
    environment = dict(os.environ, PGPASSWORD=cfg.db_password.get_secret_value())
    result = subprocess.run([postgres_tool("pg_restore"), "--host", cfg.db_host, "--port", str(cfg.db_port),
                             "--username", cfg.db_user, "--dbname", check_database,
                             "--no-owner", "--no-privileges", "--exit-on-error", str(target / "stock_alei.dump")],
                            env=environment, capture_output=True, text=True)
    if result.returncode:
        raise RuntimeError(f"恢复验证库 {check_database} 失败，生产库未改动")
    previous_name = os.environ.get("STOCK_DB_NAME")
    os.environ["STOCK_DB_NAME"] = check_database
    settings.cache_clear()
    try:
        original = metadata["original_root"]
        with db.connect() as conn:
            conn.execute("UPDATE strategies SET source_path=replace(source_path,%s,%s)", (original, str(target)))
            conn.execute("UPDATE backtest_runs SET manifest=jsonb_set(manifest,'{strategy,source_path}',to_jsonb(replace(manifest#>>'{strategy,source_path}',%s,%s)))", (original, str(target)))
            conn.execute("UPDATE backtest_runs SET manifest=jsonb_set(manifest,'{engine_source,package}',to_jsonb(replace(manifest#>>'{engine_source,package}',%s,%s))) WHERE manifest ? 'engine_source'", (original, str(target)))
            if conn.execute("SELECT to_regclass('experiments') AS name").fetchone()["name"]:
                conn.execute("UPDATE experiments SET frozen=jsonb_set(jsonb_set(frozen,'{strategy,source_path}',to_jsonb(replace(frozen#>>'{strategy,source_path}',%s,%s))),'{engine_source,package}',to_jsonb(replace(frozen#>>'{engine_source,package}',%s,%s)))",
                             (original, str(target), original, str(target)))
            # 快照中的 PID 属于原库；副本不能用它判断任务活性，更不能终止原库进程。
            stale_jobs = conn.execute("UPDATE jobs SET status='interrupted',runner_pid=NULL,error='恢复副本中的运行任务已标记中断，按检查点手动重跑',finished_at=now() WHERE status='running' RETURNING id").fetchall()
            if stale_jobs:
                conn.execute("UPDATE data_batches SET status='failed' WHERE status='staging' AND job_id=ANY(%s)", ([row["id"] for row in stale_jobs],))
        restored_counts = db.rows("SELECT r.dataset,count(*) AS rows FROM source_records r JOIN data_batches b ON b.id=r.batch_id WHERE b.status='published' GROUP BY r.dataset ORDER BY r.dataset")
        previous_run = db.one("SELECT * FROM backtest_runs WHERE result IS NOT NULL AND manifest ? 'engine_source' ORDER BY created_at DESC LIMIT 1")
        if not previous_run:
            raise ValueError("恢复库没有完整归档的成功运行，尚不能完成回测恢复验收")
        pinned = previous_run["manifest"]["data_version"]
        code = previous_run["manifest"]["config"]["codes"][0]
        row = db.records("bars:d", code=code, start=previous_run["manifest"]["config"]["start"], version=pinned)[0]
        old = row["payload"]
        corrected = dict(old)
        for key in ("open", "high", "low", "close"):
            corrected[key] = str(float(corrected[key]) * 1.03)
        # 只在独立恢复库写入标记清晰的临时修订，验证旧值读取；主库从未接收虚构数据。
        with db.connect() as conn:
            batch = conn.execute("INSERT INTO data_batches(method,arguments,status,row_count,changed_count,published_at) VALUES('restore_verification_fixture',%s,'published',1,1,now()) RETURNING id",
                                 (db.jb({"isolated": True, "note": "仅恢复验收库的版本读取验证"}),)).fetchone()["id"]
            conn.execute("INSERT INTO source_records(dataset,record_key,code,period,payload,content_hash,batch_id) VALUES(%s,%s,%s,%s,%s,%s,%s)",
                         (row["dataset"], row["record_key"], row["code"], row["period"], db.jb(corrected), hashlib.sha256(db.dumps(corrected).encode()).hexdigest(), batch))
        historical = db.payloads("bars:d", code=code, start=row["period"], end=row["period"], version=pinned)[0]
        current = db.payloads("bars:d", code=code, start=row["period"], end=row["period"])[0]
        if historical != old or current != corrected:
            raise RuntimeError("恢复后的历史版本读取不一致")
        from .engine import replay
        run_id = replay(previous_run["id"])
        engine_source = previous_run["manifest"]["engine_source"]
        from .plugins import digest_directory
        if digest_directory(Path(engine_source["package"]) / "stockapp") != engine_source["digest"]:
            raise RuntimeError("恢复后的引擎归档摘要不一致")
        runner_env = dict(os.environ, PYTHONPATH=engine_source["package"], STOCK_PROJECT_ROOT=str(target),
                          STOCK_DB_HOST=cfg.db_host, STOCK_DB_PORT=str(cfg.db_port), STOCK_DB_NAME=check_database,
                          STOCK_DB_USER=cfg.db_user, STOCK_DB_PASSWORD=cfg.db_password.get_secret_value(), PYTHONUTF8="1")
        subprocess.run([sys.executable, "-m", "stockapp.runner", run_id], env=runner_env, cwd=target, timeout=1800, check=True)
        completed = db.one("SELECT r.result,j.status,j.error FROM backtest_runs r JOIN jobs j ON j.id=r.id WHERE r.id=%s", (run_id,))
        if completed["status"] != "completed" or completed["result"] != previous_run["result"]:
            raise RuntimeError("恢复后重算不一致：" + (completed["error"] or completed["status"]))
        evidence = {"status": "verified", "check_database": check_database, "backup": str(target),
                    "original_run": str(previous_run["id"]), "replayed_run": run_id, "data_version": pinned,
                    "historical_values_preserved": True, "result_equal": True, "restored_counts": restored_counts,
                    "interrupted_snapshot_jobs": len(stale_jobs),
                    "note": "主库未修改；验证库保留，含明确标记的模拟修订；恢复使用备份中的模型及引擎文件"}
        (target / "restore-verification.json").write_text(db.dumps(evidence), encoding="utf-8")
        return evidence
    finally:
        if previous_name is None:
            os.environ.pop("STOCK_DB_NAME", None)
        else:
            os.environ["STOCK_DB_NAME"] = previous_name
        settings.cache_clear()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("operation", choices=["backup", "restore-check"])
    parser.add_argument("--directory")
    args = parser.parse_args()
    if args.operation == "backup":
        print(backup())
    else:
        if not args.directory:
            parser.error("restore-check 需要 --directory")
        evidence = restore_check(args.directory)
        print("恢复库", evidence["check_database"], "已验证：历史值保持，原模型/引擎重算结果完全一致。")


if __name__ == "__main__":
    main()
