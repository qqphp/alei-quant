"""固定同一数据/模型/规则的分段参数研究；锁定参数后才生成样本外任务。"""
from copy import deepcopy
from datetime import date
from uuid import uuid4

from . import db, engine, plugins

STAGES = ("train", "validation", "out_of_sample")


def create(name, config, intervals, candidates):
    if not name.strip() or len(name) > 100:
        raise ValueError("研究名称须为 1–100 个字符")
    if set(intervals) != set(STAGES):
        raise ValueError("必须提供训练、验证和样本外三个区间")
    previous = None
    for stage in STAGES:
        interval = intervals[stage]
        if set(interval) != {"start", "end"}:
            raise ValueError("每个区间仅接受 start/end")
        start, end = date.fromisoformat(interval["start"]), date.fromisoformat(interval["end"])
        if start >= end or start < date(2023, 1, 1) or previous and start <= previous:
            raise ValueError("区间须从 2023 年起，起点早于终点，依次排列且不能重叠")
        previous = end
    if not 1 <= len(candidates) <= 20:
        raise ValueError("一次研究接受 1–20 组参数，全部通过现有任务队列运行")
    # 用完整跨度校验所有阶段，数据/模型/规则仍只冻结一次。
    frozen = engine.freeze({**config, "start": intervals["train"]["start"], "end": intervals["out_of_sample"]["end"]})
    frozen["config"].update(intervals["train"])
    manifest = frozen["strategy"]["manifest"]
    candidates = [plugins.validate_parameters(manifest, candidate) for candidate in candidates]
    if len({db.dumps(candidate) for candidate in candidates}) != len(candidates):
        raise ValueError("参数候选不能重复")
    experiment_id = uuid4()
    with db.connect() as conn:
        conn.execute("INSERT INTO experiments(id,name,frozen,intervals,candidates) VALUES(%s,%s,%s,%s,%s)",
                     (experiment_id, name.strip(), db.jb(frozen), db.jb(intervals), db.jb(candidates)))
        for index, candidate in enumerate(candidates):
            for stage in STAGES[:2]:
                run = deepcopy(frozen)
                run["config"].update(intervals[stage], parameters=candidate)
                run["research"] = {"experiment_id": str(experiment_id), "candidate": index, "stage": stage,
                                   "account_policy": "每个区间独立以配置资金开始，不继承上一段持仓；预热只用于指标"}
                run_id = engine.persist_run(conn, run)
                conn.execute("INSERT INTO experiment_runs VALUES(%s,%s,%s,%s)", (experiment_id, index, stage, run_id))
    return str(experiment_id)


def lock_choice(experiment_id, candidate):
    with db.connect() as conn:
        experiment = conn.execute("SELECT * FROM experiments WHERE id=%s FOR UPDATE", (experiment_id,)).fetchone()
        if not experiment:
            raise ValueError("参数研究不存在")
        if experiment["selected"] is not None:
            raise ValueError("参数已锁定；已有样本外任务不能改换候选，可按原清单重跑失败任务")
        if not 0 <= candidate < len(experiment["candidates"]):
            raise ValueError("候选参数编号不存在")
        states = conn.execute("SELECT j.status FROM experiment_runs e JOIN jobs j ON j.id=e.run_id WHERE experiment_id=%s", (experiment_id,)).fetchall()
        if len(states) != len(experiment["candidates"])*2 or any(r["status"] != "completed" for r in states):
            raise ValueError("先完成全部训练和验证任务；失败或取消任务可在任务列表按原版本重跑")
        frozen = deepcopy(experiment["frozen"])
        frozen["config"].update(experiment["intervals"]["out_of_sample"], parameters=experiment["candidates"][candidate])
        frozen["research"] = {"experiment_id": str(experiment_id), "candidate": candidate, "stage": "out_of_sample",
                              "account_policy": "独立初始资金；参数在样本外运行前锁定，不继承前段持仓"}
        run_id = engine.persist_run(conn, frozen)
        conn.execute("INSERT INTO experiment_runs VALUES(%s,%s,'out_of_sample',%s)", (experiment_id, candidate, run_id))
        conn.execute("UPDATE experiments SET selected=%s,locked_at=now() WHERE id=%s", (candidate, experiment_id))
    return run_id


def detail(experiment_id):
    experiment = db.one("SELECT id,name,intervals,candidates,selected,locked_at,created_at,"
                        "frozen->>'data_version' AS data_version,frozen->>'engine_version' AS engine_version "
                        "FROM experiments WHERE id=%s", (experiment_id,))
    if not experiment:
        raise ValueError("参数研究不存在")
    experiment["runs"] = db.rows("SELECT e.candidate,e.stage,e.run_id,j.status,j.error,r.result->'metrics' AS metrics "
                                 "FROM experiment_runs e JOIN jobs j ON j.id=e.run_id JOIN backtest_runs r ON r.id=e.run_id "
                                 "WHERE experiment_id=%s ORDER BY e.candidate,CASE e.stage WHEN 'train' THEN 0 WHEN 'validation' THEN 1 ELSE 2 END", (experiment_id,))
    return experiment
