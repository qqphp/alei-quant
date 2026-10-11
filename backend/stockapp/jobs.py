from uuid import uuid4

from . import db


class Cancelled(Exception):
    pass


class BudgetPaused(Exception):
    pass


class CollectionSlice(Exception):
    pass


class SourceRetry(RuntimeError):
    pass


def submit(kind, config):
    job_id = uuid4()
    db.execute("INSERT INTO jobs(id,kind,config) VALUES(%s,%s,%s)", (job_id, kind, db.jb(config)))
    return str(job_id)


def log(job_id, message, level="info"):
    db.execute("INSERT INTO job_logs(job_id,level,message) VALUES(%s,%s,%s)", (job_id, level, message[:3000]))


def heartbeat(job_id, progress=None, total=None, checkpoint=None):
    fields, args = ["heartbeat_at=now()"], []
    for field, value in (("progress", progress), ("total", total), ("checkpoint", checkpoint)):
        if value is not None:
            fields.append(field + "=%s")
            args.append(db.jb(value) if field == "checkpoint" else value)
    job = db.one("UPDATE jobs SET " + ",".join(fields) + " WHERE id=%s RETURNING cancel_requested", [*args, job_id])
    if not job or job["cancel_requested"]:
        raise Cancelled("任务已取消")


def finish(job_id, status, error=None):
    with db.connect() as conn:
        conn.execute("UPDATE jobs SET status=%s,error=%s,finished_at=now(),heartbeat_at=now() WHERE id=%s", (status, error, job_id))
        if status != "completed":
            conn.execute("UPDATE data_batches SET status='failed' WHERE job_id=%s AND status='staging'", (job_id,))

