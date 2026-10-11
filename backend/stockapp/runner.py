import os
import sys
import traceback
from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

from . import db, jobs


def main(job_id):
    job = db.one("SELECT * FROM jobs WHERE id=%s", (job_id,))
    if not job:
        raise ValueError("任务不存在")
    db.execute("UPDATE jobs SET status='running',started_at=now(),heartbeat_at=now(),runner_pid=%s WHERE id=%s", (os.getpid(), job_id))
    try:
        jobs.heartbeat(job_id)
        if job["kind"] == "collect":
            from .provider import collect
            collect(job)
        else:
            from .engine import backtest
            backtest(job)
        jobs.heartbeat(job_id)
        jobs.finish(job_id, "completed")
    except jobs.CollectionSlice as exc:
        jobs.finish(job_id, "waiting", str(exc))
        db.execute("UPDATE jobs SET retry_after=now()+interval '10 seconds',finished_at=NULL WHERE id=%s", (job_id,))
        jobs.log(job_id, str(exc))
    except jobs.SourceRetry as exc:
        checkpoint = db.one("SELECT checkpoint FROM jobs WHERE id=%s", (job_id,))["checkpoint"]
        attempts = checkpoint.get("retry_count", 0) + 1
        checkpoint["retry_count"] = attempts
        db.execute("UPDATE jobs SET checkpoint=%s WHERE id=%s", (db.jb(checkpoint), job_id))
        if attempts <= 3:
            jobs.finish(job_id, "waiting", f"{exc}；第 {attempts}/3 次退避重试")
            db.execute("UPDATE jobs SET retry_after=now()+(%s*interval '1 minute'),finished_at=NULL WHERE id=%s", (attempts, job_id))
            jobs.log(job_id, f"接收中断，{attempts} 分钟后从检查点重试；未发布当前批次。")
        else:
            jobs.finish(job_id, "failed", f"{exc}；连续重试已用尽，请检查来源后手动重跑")
            jobs.log(job_id, "连续接收中断，自动重试已停止，保留已发布批次。", "error")
    except jobs.BudgetPaused as exc:
        next_day = datetime.now(ZoneInfo("Asia/Shanghai")).date() + timedelta(days=1)
        resume = datetime.combine(next_day, time(0, 5), ZoneInfo("Asia/Shanghai"))
        jobs.finish(job_id, "waiting", str(exc))
        db.execute("UPDATE jobs SET retry_after=%s,finished_at=NULL WHERE id=%s", (resume, job_id))
        jobs.log(job_id, str(exc))
    except jobs.Cancelled:
        jobs.finish(job_id, "cancelled")
        jobs.log(job_id, "任务已取消；保留已发布数据和检查点。")
    except Exception as exc:
        # 原始连接字符串/配置不写日志。异常栈仅输出模块位置，业务消息保留。
        safe = str(exc) if isinstance(exc, (ValueError, RuntimeError)) or type(exc).__name__ == "SourceError" else f"{type(exc).__name__}：执行失败，请检查本机服务及输入"
        jobs.finish(job_id, "failed", safe[:2000])
        jobs.log(job_id, safe, "error")
        print(safe, flush=True)
        for frame in traceback.extract_tb(exc.__traceback__, limit=3):
            print(f"{frame.filename}:{frame.lineno} {frame.name}", flush=True)


if __name__ == "__main__":
    main(sys.argv[1])

