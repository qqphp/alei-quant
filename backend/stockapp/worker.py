"""持久化任务调度；每个任务独立进程，采集额外受全局连接锁约束。"""
import os
import subprocess
import sys
import time
import psutil
from pathlib import Path
from . import plugins

from . import db, jobs
from .config import ROOT
from .scheduler import tick


def stop_child(child):
    """终止本调度器创建的进程树，包含 Windows 的 venv 启动器后代。"""
    try:
        root = psutil.Process(child.pid)
        processes = root.children(recursive=True) + [root]
    except psutil.NoSuchProcess:
        child.wait()
        return
    for process in reversed(processes):
        try:
            process.terminate()
        except psutil.NoSuchProcess:
            pass
    _, alive = psutil.wait_procs(processes, timeout=5)
    for process in alive:
        try:
            process.kill()
        except psutil.NoSuchProcess:
            pass
    child.wait(timeout=10)


def launch(job):
    environment = dict(os.environ)
    run = db.one("SELECT manifest FROM backtest_runs WHERE id=%s", (job["id"],))
    if run and run["manifest"].get("engine_source"):
        source = run["manifest"]["engine_source"]
        package = Path(source["package"]).resolve()
        if not package.is_relative_to((ROOT / "runtime" / "engines").resolve()) or plugins.digest_directory(package / "stockapp") != source["digest"]:
            raise ValueError("引擎归档文件校验失败")
        environment["PYTHONPATH"] = str(package)
        environment["STOCK_PROJECT_ROOT"] = str(ROOT)
    child = subprocess.Popen([sys.executable, "-m", "stockapp.runner", str(job["id"])], cwd=ROOT, env=environment)
    limits = run["manifest"].get("resource_limits", {}) if run else {}
    return {"child": child, "kind": job["kind"], "progress": job["progress"],
            "memory_bytes": limits.get("memory_bytes", 2*1024**3),
            "deadline": time.monotonic() + limits.get("timeout_seconds", 3600)}


def main():
    with db.connect() as lock:
        if not lock.execute("SELECT pg_try_advisory_lock(81432002) AS ok").fetchone()["ok"]:
            raise RuntimeError("已有平台工作进程运行")
        lock.commit()
        # 只有确认旧进程不存在后才将遗留任务标记中断。
        for old in db.rows("SELECT id,runner_pid FROM jobs WHERE status='running'"):
            try:
                if old["runner_pid"] and psutil.pid_exists(old["runner_pid"]) and str(old["id"]) in " ".join(psutil.Process(old["runner_pid"]).cmdline()):
                    raise RuntimeError("旧任务进程仍在运行，暂不启动第二个工作进程")
                jobs.finish(old["id"], "interrupted", "工作进程中断，请按原配置重跑")
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                jobs.finish(old["id"], "interrupted", "工作进程中断，请按原配置重跑")
        print("本地工作进程已启动：1 个采集槽、2 个回测槽", flush=True)
        active = {}
        next_schedule = 0
        try:
            while True:
                if time.monotonic() >= next_schedule:
                    tick()
                    next_schedule = time.monotonic() + 60
                db.execute("UPDATE jobs SET status='queued',error=NULL,retry_after=NULL WHERE status='waiting' AND retry_after<=now() AND NOT cancel_requested")
                for job_id, task in list(active.items()):
                    child = task["child"]
                    state = db.one("SELECT status,cancel_requested,progress FROM jobs WHERE id=%s", (job_id,))
                    rss = 0
                    if child.poll() is None:
                        try:
                            process = psutil.Process(child.pid)
                            for member in [process, *process.children(recursive=True)]:
                                try:
                                    rss += member.memory_info().rss
                                except psutil.NoSuchProcess:
                                    pass
                        except psutil.NoSuchProcess:
                            pass
                        db.execute("UPDATE jobs SET peak_rss=greatest(peak_rss,%s) WHERE id=%s", (rss, job_id))
                    memory_exceeded = task["kind"] == "backtest" and rss > task["memory_bytes"]
                    if child.poll() is None and (state["cancel_requested"] or memory_exceeded or time.monotonic() > task["deadline"]):
                        stop_child(child)
                        current = db.one("SELECT status FROM jobs WHERE id=%s", (job_id,))
                        if current["status"] == "running":
                            if memory_exceeded and not state["cancel_requested"]:
                                jobs.finish(job_id, "failed", "回测超过清单内存上限，已停止该任务；请缩小股票池或历史窗口")
                            elif not state["cancel_requested"] and task["kind"] == "collect" and state["progress"] > task["progress"]:
                                jobs.finish(job_id, "waiting", "采集已达到单次一小时预算，一分钟后按检查点继续")
                                db.execute("UPDATE jobs SET retry_after=now()+interval '1 minute',finished_at=NULL WHERE id=%s", (job_id,))
                            else:
                                jobs.finish(job_id, "cancelled" if state["cancel_requested"] else "failed", "用户取消" if state["cancel_requested"] else "任务超过一小时且未取得进展，保留检查点供重跑")
                    if child.poll() is not None:
                        current = db.one("SELECT status FROM jobs WHERE id=%s", (job_id,))
                        if current["status"] == "running":
                            jobs.finish(job_id, "interrupted", "任务子进程退出但未提交结果，请重跑")
                        del active[job_id]
                for kind, capacity in (("collect", 1), ("backtest", 2)):
                    used = sum(task["kind"] == kind for task in active.values())
                    for _ in range(capacity - used):
                        with db.connect() as conn:
                            job = conn.execute("SELECT id,kind,progress FROM jobs WHERE status='queued' AND kind=%s ORDER BY COALESCE(started_at,created_at),created_at,id FOR UPDATE SKIP LOCKED LIMIT 1", (kind,)).fetchone()
                            if job:
                                conn.execute("UPDATE jobs SET status='running',heartbeat_at=now() WHERE id=%s", (job["id"],))
                        if not job:
                            break
                        try:
                            active[job["id"]] = launch(job)
                        except (ValueError, OSError):
                            jobs.finish(job["id"], "failed", "任务启动或引擎归档校验失败，请检查本机文件")
                time.sleep(1)
        finally:
            for job_id, task in active.items():
                if task["child"].poll() is None:
                    stop_child(task["child"])
                if db.one("SELECT status FROM jobs WHERE id=%s", (job_id,))["status"] == "running":
                    jobs.finish(job_id, "interrupted", "工作进程已停止，请按原配置重跑")


if __name__ == "__main__":
    main()

