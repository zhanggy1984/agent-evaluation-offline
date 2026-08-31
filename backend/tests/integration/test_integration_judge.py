"""P2-D3 judge worker 队列集成测试（真库 + fake JudgeClient）。

覆盖 worker.py 队列逻辑（test_judge.py 未覆盖的层）：
- _claim_batch 认领：pending → processing + claim_id + lease_until、limit 截断、future next_retry_at 跳过
- _reclaim_stale 回收：processing 租约过期 → 回 pending（claim_id/lease 清空）
- _process_one 判分：成功 done 全字段 / judge 抛错重试退避 / 超限 failed / 结果不可判 failed 不调 judge
- _run_judge_done 收尾判定：全终态 / 有未决 / 无任务

判分走 fake client（JudgeClient 真实 HTTP 往返由 tests/test_judge_client.py 覆盖）；
dimension/rubric 种子已在共享库（factuality 1.2 全局默认，load_rubric 通用兜底）。
"""
import types
from datetime import timedelta

import pytest

pytest.importorskip("aiomysql")

from sqlalchemy import delete, select

from app.core.db import SessionLocal
from app.judge import worker
from app.judge.client import JudgeError, JudgeVerdict
from app.judge.rubric import RATINGS
from app.models import CaseVersion, EvalResult, JudgeTask
from helpers import create_chain, make_run


async def _seed_judge_env(env, db, *, case_names=None, max_retries=2, answer="A 售价 100 元",
                          pass_fail="pass", task_status="pending"):
    """播种：chain + completed run + 每 case 一条 case_version/eval_result/judge_task(factuality)。"""
    ch = await create_chain(db, case_names=case_names or ["it72-case"])
    run = make_run(ch["agent"].id, ch["suite"].id,
                   run_config={"judge_max_retries": max_retries})
    run.status = "completed"
    db.add(run)
    await db.flush()
    tasks = []
    for case in ch["cases"]:
        cv = CaseVersion(case_id=case.id, version_no=1, content_hash="0" * 64,
                         snapshot={"input": case.input, "expected": {"golden_answer": "A 售价 100 元"}})
        db.add(cv)
        await db.flush()
        res = EvalResult(run_id=run.id, case_id=case.id, case_version_id=cv.id,
                         pass_fail=pass_fail, answer=answer)
        db.add(res)
        await db.flush()
        task = JudgeTask(run_id=run.id, case_id=case.id, dimension_code="factuality",
                         status=task_status)
        db.add(task)
        tasks.append(task)
    await db.commit()
    env.agents.append(ch["agent"]); env.interfaces.append(ch["iface"])
    env.suites.append(ch["suite"]); env.cases.extend(ch["cases"]); env.runs.append(run)
    return run, tasks[0], ch


def _fake_judge(verdict=None, exc=None, responses=None):
    """fake JudgeClient：judge 返回固定 verdict、固定抛异常，或按 responses 序列逐次返回/抛错。

    真实 HTTP 由单元文件覆盖；responses 供 P0-1 多次采样测试模拟每轮判定（JudgeVerdict 或 JudgeError）。
    """
    if responses is not None:
        it = iter(responses)
        async def judge(**kwargs):
            item = next(it)
            if isinstance(item, Exception):
                raise item
            return item
        return types.SimpleNamespace(judge=judge)
    async def judge(**kwargs):
        if exc is not None:
            raise exc
        return verdict
    return types.SimpleNamespace(judge=judge)


async def _load_task(db, run_id):
    """独立 session 读取最新（REPEATABLE READ：复用 db 会在同一事务快照读到陈旧值）。"""
    async with SessionLocal() as s:
        return (await s.execute(select(JudgeTask).where(JudgeTask.run_id == run_id))).scalars().first()


async def _detached_task(run_id: int) -> JudgeTask:
    """独立 session 加载 + detach（模拟认领 session 关闭后传给 _process_one 的对象）。"""
    async with SessionLocal() as s:
        return (await s.execute(select(JudgeTask).where(JudgeTask.run_id == run_id))).scalars().first()


# ---------------- _claim_batch 认领 ----------------
@pytest.mark.asyncio(loop_scope="session")
async def test_claim_batch_claims_pending(db, env):
    _, task, _ = await _seed_judge_env(env, db)
    claimed = await worker._claim_batch(db, 10, worker._now())
    assert len(claimed) == 1
    assert task.status == "processing"
    assert task.claim_id
    assert task.lease_until is not None


@pytest.mark.asyncio(loop_scope="session")
async def test_claim_batch_limit(db, env):
    run, _, _ = await _seed_judge_env(env, db, case_names=["c1", "c2", "c3"])
    claimed = await worker._claim_batch(db, 2, worker._now())
    assert len(claimed) == 2
    statuses = (await db.execute(select(JudgeTask.status).where(JudgeTask.run_id == run.id))).scalars().all()
    assert sorted(statuses) == ["pending", "processing", "processing"]


@pytest.mark.asyncio(loop_scope="session")
async def test_claim_batch_skips_future_retry(db, env):
    """next_retry_at 未到期 → 不认领（重试退避中的任务不抢跑）。"""
    _, task, _ = await _seed_judge_env(env, db)
    task.next_retry_at = worker._now() + timedelta(seconds=60)
    await db.commit()
    claimed = await worker._claim_batch(db, 10, worker._now())
    assert claimed == []


# ---------------- _reclaim_stale 回收 ----------------
@pytest.mark.asyncio(loop_scope="session")
async def test_reclaim_stale(db, env):
    """processing 租约过期 → 回 pending（worker 崩溃/重启后任务可被再次认领）。"""
    _, task, _ = await _seed_judge_env(env, db, task_status="processing")
    task.claim_id = "dead-claim"
    task.lease_until = worker._now() - timedelta(seconds=10)
    await db.commit()
    n = await worker._reclaim_stale(db, worker._now())
    assert n == 1
    fresh = await _load_task(db, task.run_id)
    assert fresh.status == "pending"
    assert fresh.claim_id is None
    assert fresh.lease_until is None


# ---------------- _process_one 判分 ----------------
@pytest.mark.asyncio(loop_scope="session")
async def test_process_one_done(db, env):
    """P0-1 多次采样：同一 verdict ×3 → 聚合 done；顶层兼容字段 + repeats 明细。"""
    run, _, ch = await _seed_judge_env(env, db)
    verdict = JudgeVerdict(dimension="factuality", level=4, score=RATINGS[4] * 100,
                           reason="事实准确", rubric_version="1.2")
    await worker._process_one(await _detached_task(run.id),
                              {"judge_max_retries": 2, "judge_repeat": 3},
                              ch["iface"].id, _fake_judge(verdict=verdict))
    fresh = await _load_task(db, run.id)
    assert fresh.status == "done"
    assert fresh.claim_id is None and fresh.lease_until is None
    # 顶层保持 scorer 兼容字段（factuality/metric 零改动消费）
    assert fresh.result["dimension"] == "factuality"
    assert fresh.result["level"] == 4
    assert fresh.result["score"] == 80.0
    assert fresh.result["reason"] == "事实准确"
    assert fresh.result["rubric_version"] == "1.2"
    # P0-1 新增：判 3 次 → repeat=实际采样数 3、repeats 明细 3 条
    assert fresh.result["repeat"] == 3
    assert len(fresh.result["repeats"]) == 3
    assert fresh.result["repeats"][0]["level"] == 4


@pytest.mark.asyncio(loop_scope="session")
async def test_process_one_majority_picks_common_level(db, env):
    """P0-1 档位多数决：3 次 [4,4,3] → 聚合 4；reason 取多数档样本；离群 3 保留在 repeats。"""
    run, _, ch = await _seed_judge_env(env, db)
    v4a = JudgeVerdict(dimension="factuality", level=4, score=80.0, reason="答案准确", rubric_version="1.2")
    v4b = JudgeVerdict(dimension="factuality", level=4, score=80.0, reason="与参考一致", rubric_version="1.2")
    v3 = JudgeVerdict(dimension="factuality", level=3, score=60.0, reason="有明显缺陷", rubric_version="1.2")
    await worker._process_one(await _detached_task(run.id),
                              {"judge_max_retries": 2, "judge_repeat": 3},
                              ch["iface"].id, _fake_judge(responses=[v4a, v4b, v3]))
    fresh = await _load_task(db, run.id)
    assert fresh.status == "done"
    assert fresh.result["level"] == 4 and fresh.result["score"] == 80.0
    assert fresh.result["reason"] == "答案准确"  # 多数档样本，不拼接离群「缺陷」理由
    assert fresh.result["repeat"] == 3
    assert [r["level"] for r in fresh.result["repeats"]] == [4, 4, 3]


@pytest.mark.asyncio(loop_scope="session")
async def test_process_one_partial_failure_still_done(db, env):
    """P0-1 部分失败：1 次抛错 + 2 次成功 → 成功 2 ≥ 阈值 2 → done；repeat=实际采样 2（可识别退化）。"""
    run, _, ch = await _seed_judge_env(env, db)
    v4 = JudgeVerdict(dimension="factuality", level=4, score=80.0, reason="准确", rubric_version="1.2")
    await worker._process_one(await _detached_task(run.id),
                              {"judge_max_retries": 2, "judge_repeat": 3},
                              ch["iface"].id,
                              _fake_judge(responses=[JudgeError("偶发超时"), v4, v4]))
    fresh = await _load_task(db, run.id)
    assert fresh.status == "done"
    assert fresh.result["level"] == 4
    assert fresh.result["repeat"] == 2  # 实际采样数 < 配置 3，evidence 可识别
    assert len(fresh.result["repeats"]) == 2


@pytest.mark.asyncio(loop_scope="session")
async def test_process_one_under_threshold_retries(db, env):
    """P0-1 成功采样 < 阈值（1 < 2）→ 不足多数决，整 task 重采样重试（attempts++，回 pending）。"""
    run, _, ch = await _seed_judge_env(env, db)
    v4 = JudgeVerdict(dimension="factuality", level=4, score=80.0, reason="准确", rubric_version="1.2")
    await worker._process_one(await _detached_task(run.id),
                              {"judge_max_retries": 2, "judge_repeat": 3},
                              ch["iface"].id,
                              _fake_judge(responses=[JudgeError("挂"), JudgeError("挂"), v4]))
    fresh = await _load_task(db, run.id)
    assert fresh.status == "pending"  # 不足阈值 → 走重试，不 done
    assert fresh.attempts == 1


@pytest.mark.asyncio(loop_scope="session")
async def test_process_one_repeat2_single_success_retries(db, env):
    """P0-1 补强：repeat=2 时 1 成功 1 失败 → 成功 1 < 阈值 2 → 重试（不静默单样本 done）。

    修复 done 阈值 ceil(repeat/2) 在 repeat=2 退化为 1 的缺陷——否则此处会静默 done 且
    result.repeat=1，对冲完全失效。"""
    run, _, ch = await _seed_judge_env(env, db)
    v4 = JudgeVerdict(dimension="factuality", level=4, score=80.0, reason="准确", rubric_version="1.2")
    await worker._process_one(await _detached_task(run.id),
                              {"judge_max_retries": 2, "judge_repeat": 2},
                              ch["iface"].id,
                              _fake_judge(responses=[JudgeError("偶发超时"), v4]))
    fresh = await _load_task(db, run.id)
    assert fresh.status == "pending"  # 单样本不足多数决 → 重采样重试
    assert fresh.attempts == 1


@pytest.mark.asyncio(loop_scope="session")
async def test_process_one_retry_then_failed(db, env):
    """judge 抛错：attempts 递增；< max_retries → 回 pending + 退避 next_retry_at；≥ → failed。"""
    run, _, ch = await _seed_judge_env(env, db, max_retries=2)
    client = _fake_judge(exc=JudgeError("LLM 挂了"))
    # 真实 worker 每轮 drain 从 DB 重新认领加载对象，故每次调用传入新加载的 detached 对象
    # 第 1 次：attempts 0→1 < 2 → pending + next_retry_at
    await worker._process_one(await _detached_task(run.id), {"judge_max_retries": 2},
                              ch["iface"].id, client)
    t1 = await _load_task(db, run.id)
    assert t1.status == "pending" and t1.attempts == 1
    assert t1.next_retry_at is not None
    # 第 2 次：attempts 1→2 ≥ 2 → failed
    await worker._process_one(await _detached_task(run.id), {"judge_max_retries": 2},
                              ch["iface"].id, client)
    t2 = await _load_task(db, run.id)
    assert t2.status == "failed" and t2.attempts == 2


@pytest.mark.asyncio(loop_scope="session")
async def test_process_one_unjudgeable(db, env):
    """EvalResult pass_fail=error → 判分无意义，直接 failed 且不调 judge。"""
    run, _, ch = await _seed_judge_env(env, db, pass_fail="error")
    called = []

    async def judge(**kwargs):
        called.append(1)
        return JudgeVerdict(dimension="factuality", level=4, score=80.0, reason="x", rubric_version="1.2")
    await worker._process_one(await _detached_task(run.id), {"judge_max_retries": 2},
                              ch["iface"].id, types.SimpleNamespace(judge=judge))
    fresh = await _load_task(db, run.id)
    assert fresh.status == "failed"
    assert called == []


# ---------------- _run_judge_done 收尾判定 ----------------
@pytest.mark.asyncio(loop_scope="session")
async def test_run_judge_done(db, env):
    run, task, _ = await _seed_judge_env(env, db)
    # 全 pending → 未就绪
    assert await worker._run_judge_done(db, run.id) is False
    # 全终态（done）→ 就绪
    task.status = "done"
    task.result = {"level": 4}
    await db.commit()
    assert await worker._run_judge_done(db, run.id) is True
    # 无任务 → 不就绪
    await db.execute(delete(JudgeTask).where(JudgeTask.run_id == run.id))
    await db.commit()
    assert await worker._run_judge_done(db, run.id) is False
