"""O-G.2 / O-E.7：scanner 回收 **error 回归 run** 时短路 salvage（容器真库）。

§6.6 逐字：scanner 回收 error_regression run 时**短路 salvage**，并执行替代收尾动作。

反例（本文件要挡住的）：短路缺失时 `_reap_one_pass` 对超时 run 无条件调
`score_run_salvage`，按**普通 run 语义**重算 error run —— 改写已终值行的 `pass_fail`、
把未完成 case 回填成 `pass_fail='error'`、落 `agent_score`。而 error run 的取值域是
{pass, fail, na}、**绝不落 'error'**（§3.3），且不得产分。

两条回收路径（租约 / 硬超时）**都**要覆盖：守卫若只加在其中一处，另一处仍会污染。

两条路径的 `total_case` **同源不同值**，但回填行为现在**都成立**：
`total_case` 于**建单处**写入 `len(selected)`（#260，过滤前计划数），接管时被覆写为
`len(_load_run_cases(...))`（过滤后加载数）。取大者只会让判据**更容易触发**，而补行循环
遍历的是加载集（已过滤）⇒ 不产生多余结果行。故两条路径都断言「未完成 case 回填 na」。

⚠️ 本文件曾记录「建单处不写 `total_case` ⇒ 租约路径下回填不发生」的独立缺口（#260），
该缺口已于 2026-09-15 修复，原「此处不对它断言」的措辞随之失效 —— 用例② 现断言回填。
"""
import pytest

pytest.importorskip("aiomysql")

from datetime import datetime, timedelta  # noqa: E402

from sqlalchemy import select  # noqa: E402

from app.core.db import SessionLocal  # noqa: E402
from app.models import CaseVersion, EvalResult, EvalRun  # noqa: E402
from app.runner.orchestrator import (  # noqa: E402
    _create_error_regression_run_locked,
    _lock_agent,
    orchestrator,
)
from app.runner.scanner import _reap_one_pass  # noqa: E402
from helpers import create_chain, make_case, make_run  # noqa: E402

# §8.1 error case 唯一允许的断言算子（形态不符的 case 不会被 error run 加载）
_ERROR_ASSERTION = {"dimension": "leakage", "op": "keyword_not_contains",
                    "args": {"path": "answer", "keywords": ["内部编号"]}}


def _utcnow():
    return datetime.utcnow()


def _patch_cancel(monkeypatch):
    cancelled: list[int] = []
    monkeypatch.setattr(orchestrator, "cancel_run", lambda rid: cancelled.append(rid))
    return cancelled


async def _get_run(run_id):
    async with SessionLocal() as s:
        return await s.get(EvalRun, run_id)


async def _seed_error_chain(env, db, names):
    """造 error 侧链路：case 须 `case_type` 非空 + 断言为 keyword_not_contains（§8.1）。

    `make_case` 无 `case_type` 形参（该列是 error 域后加的），故造完就地补。
    """
    ch = await create_chain(db, case_names=names, case_kw={
        "assertions": [_ERROR_ASSERTION],
        # 维度要与断言算子同域（leakage）：否则评分算不出分、`agent_score` 恒 None，
        # 那条断言就失去判别力（反向对照实测——它会跟着一起绿）。
        "metrics": {"leakage": {"enabled": True}},
    })
    for c in ch["cases"]:
        c.case_type = "factuality"
    env.agents.append(ch["agent"])
    env.interfaces.append(ch["iface"])
    env.suites.append(ch["suite"])
    env.cases.extend(ch["cases"])
    await db.commit()
    return ch


def _make_error_run(ch, *, total_case=None):
    """error 回归 run。`total_case` 显式传入以区分「已被接管」/「从未被接管」两种现场。

    `make_run` 硬编码 `trigger_type="manual"`；此处覆写而非给它加形参——
    共享夹具多一个只为本文件存在的开关，收益不抵它对既有 11 个调用点的回归面。
    """
    run = make_run(ch["agent"].id, ch["suite"].id)
    run.trigger_type = "error_regression"
    run.pinned = True                                   # §7.4：关键版本不被清理
    run.case_ids = [c.id for c in ch["cases"]]
    if total_case is not None:
        run.total_case = total_case
    return run


async def _make_result(db, run_id, case, *, pass_fail="pass"):
    """预置一条已采集 eval_result（对账/改写的观察对象）。"""
    stmt = (select(CaseVersion).where(CaseVersion.case_id == case.id)
            .order_by(CaseVersion.version_no.desc()).limit(1))
    cv = (await db.execute(stmt)).scalars().first()
    if cv is None:
        cv = CaseVersion(case_id=case.id, version_no=1, content_hash="it256",
                         snapshot={"input": case.input, "expected": case.expected,
                                   "assertions": case.assertions, "metrics": case.metrics})
        db.add(cv)
        await db.flush()
    er = EvalResult(run_id=run_id, case_id=case.id, case_version_id=cv.id,
                    pass_fail=pass_fail, answer="ok", score_per_dimension=[],
                    ttft_p50=0.5, ttft_p95=0.6, e2e_p50=1.0, e2e_p95=1.2)
    db.add(er)
    await db.flush()
    return er


async def _results_of(run_id):
    async with SessionLocal() as s:
        return (await s.execute(select(EvalResult).where(
            EvalResult.run_id == run_id))).scalars().all()


# ---------------- ①硬超时路径（run 已被接管 ⇒ total_case 已写） ----------------

@pytest.mark.asyncio(loop_scope="session")
async def test_reap_error_run_hard_deadline_short_circuits_salvage(db, env, monkeypatch):
    """硬超时回收 error run：终态保持 timeout、**不产分**、未完成 case 回填 na。"""
    ch = await _seed_error_chain(env, db, ["it256-dl-a", "it256-dl-b"])
    now = _utcnow()
    run = _make_error_run(ch, total_case=2)      # 被接管过 ⇒ 接管 UPDATE 已写 total_case
    run.status = "running"
    run.lease_until = now + timedelta(seconds=60)   # 租约未过，只命中硬超时
    run.hard_deadline = now - timedelta(seconds=1)
    db.add(run); await db.flush(); env.runs.append(run)
    await _make_result(db, run.id, ch["cases"][0], pass_fail="pass")   # case A 已采集
    await db.commit()

    cancelled = _patch_cancel(monkeypatch)
    reaped = await _reap_one_pass()
    assert reaped == 1
    assert run.id in cancelled

    r = await _get_run(run.id)
    assert r.trigger_type == "error_regression"   # 夹具确实造出了 error run（反假绿）
    assert r.status == "timeout"                  # 外部先置终态不被覆盖
    # 此处**不**断言 `agent_score is None`：反向对照实测「短路与否它都是 None」⇒ 无判别力，
    # 留着等于把假绿包装成判据。§6.6「不产分」的判别力由下方 error_case / 结果行两条承载。
    assert r.error_case == 0                      # error run 不落 'error' 计数

    ers = await _results_of(run.id)
    by_case = {e.case_id: e for e in ers}
    assert by_case[ch["cases"][0].id].pass_fail == "pass"   # 已终值行原样保留
    back = by_case.get(ch["cases"][1].id)
    assert back is not None, "未完成 case 未被对账回填"
    assert back.pass_fail == "na"                 # ★ 等于 'na'（而非「不为 'error'」）
    assert back.error_type == "scheduler_unexecuted"


# ---------------- ②租约路径（pending 从未被接管 ⇒ total_case 只可能来自建单处） ----------------

@pytest.mark.asyncio(loop_scope="session")
async def test_reap_error_run_pending_lease_short_circuits_salvage(db, env, monkeypatch):
    """租约回收 pending error run（orchestrator 未接管）：**不产分、不落 'error' 行**、未完成 case 回填 na。

    ⚠️ 本用例的 run **经真实建单器**产出，不是手搓夹具 —— #260 的缺陷面恰恰是「建单处
    不写 `total_case`」，夹具若替它写，则实现里去掉那一行断言**照样绿**（零判别力）。
    真实建单器要求调用方**已持 agent 行锁**（`_create_error_regression_run_locked` 的
    契约：再取锁即同进程自锁死），故此处先 `_lock_agent`。
    """
    ch = await _seed_error_chain(env, db, ["it260-pd-a", "it260-pd-b"])
    sig = make_run(ch["agent"].id, ch["suite"].id)   # 信号 run（consumed 锚，FK 需要有真行）
    db.add(sig); await db.flush(); env.runs.append(sig)
    # ⚠️ 必须先 commit：未提交事务在 agent 父行上持 S 锁（eval_run.agent_id 外键），
    # 下面 `_lock_agent` 在**另一 session** 求 X 锁 ⇒ 跨 session 自锁，等锁 50s 后超时。
    await db.commit()
    async with SessionLocal() as s:
        assert await _lock_agent(s, ch["agent"].id)
        run_id, overflow = await _create_error_regression_run_locked(
            s, agent_id=ch["agent"].id, suite_id=ch["suite"].id,
            version="it260-v1", signal_run_id=sig.id, cap=10)
    assert run_id is not None and overflow == [], "建单器未产出 run（夹具前提不成立）"

    # 在 fixture 自己的 session 里取回（建单器用的是另一个 session），便于后续改写与清理
    run = (await db.execute(select(EvalRun).where(EvalRun.id == run_id))).scalars().one()
    env.runs.append(run)
    now = _utcnow()
    run.status = "pending"
    run.lease_until = now - timedelta(seconds=60)   # 建单即写 +90s 租约，此处已过期
    await db.commit()
    # 预置已采集行是**必须的**：无结果行时 salvage 无事可做、短路与否都无差异
    # ⇒ 「结果行集合为空」那条断言反向对照下照样绿（零判别力，实测）。
    await _make_result(db, run.id, ch["cases"][0], pass_fail="pass")
    await db.commit()

    cancelled = _patch_cancel(monkeypatch)
    reaped = await _reap_one_pass()
    assert reaped == 1
    assert run.id in cancelled

    r = await _get_run(run.id)
    assert r.trigger_type == "error_regression"
    assert r.status == "timeout"
    # 此处**不**断言 `agent_score is None`：反向对照实测「短路与否它都是 None」⇒ 无判别力，
    # 留着等于把假绿包装成判据。§6.6「不产分」的判别力由下方 error_case / 结果行两条承载。
    assert r.error_case == 0
    # #260：建单处已写计划数（本 run 从未被接管 ⇒ 这是 total_case 的唯一来源）
    assert r.total_case == 2, "建单处未写 total_case ⇒ 租约路径的对账判据恒假"
    ers = await _results_of(run.id)
    by_case = {e.case_id: e for e in ers}
    # 短路生效的**排他**证据：salvage 会按普通 run 语义重算已采集行（实测 fail/pass → na）
    assert by_case[ch["cases"][0].id].pass_fail == "pass", "已采集行被重算 ⇒ 走了 salvage（未短路）"
    # §6.6 未完成 case 回填 na —— #260 前此断言**无法成立**（total_case 恒 0 ⇒ 不触发对账）
    back = by_case.get(ch["cases"][1].id)
    assert back is not None, "未完成 case 未被对账回填（total_case 未写？）"
    assert back.pass_fail == "na"                 # ★ 等于 'na'（而非「不为 'error'」）
    assert back.error_type == "scheduler_unexecuted"


# ------------- ④计划数 > 加载数（形态不符 case 不得产结果行，#260 尾项） -------------

@pytest.mark.asyncio(loop_scope="session")
async def test_reap_error_run_planned_gt_loaded_no_extra_result(db, env, monkeypatch):
    """**计划数 > 加载数**时：判据用计划数触发对账，补行只落加载集 ⇒ 形态不符 case 无结果行。

    #260 建单处补写的 `total_case` 取的是**过滤前计划数**，而 `_is_error_case` 形态过滤
    发生在 `_load_run_cases` 加载时 ⇒ 该列**故意可能大于**加载数。这条用例就是为那个
    分支而写（前三条用例里 case 全部通过形态过滤、`selected` 与加载数恒等 ⇒ 触不到）。

    判别力所系的两条断言：
    - 形态不符 case **不得有结果行** —— 补行若照**计划集**遍历（而非加载集）就会多插一行；
    - 收尾后 `total_case` **回落到结果数**（三阶段语义：建单计划数 → 接管加载数 → 收尾结果数）。

    造「形态不符」= `case_type` 非空（**能进建单计划集**）+ `status='active'` + 断言算子
    不是 `keyword_not_contains`（§8.1）。两者缺一都不成立：只改算子会被建单 SQL 的
    `case_type IS NOT NULL` 挡下，只改 case_type 会被 `_is_error_case` 放行。
    """
    ch = await _seed_error_chain(env, db, ["it260-pg-a", "it260-pg-b"])
    # 第 3 条：形态不符（case_type 非空 ⇒ 进计划集；算子 keyword_contains ⇒ 加载时被剔）
    nc = make_case(ch["suite"].id, ch["iface"].id, "it260-pg-nc", assertions=[
        {"dimension": "leakage", "op": "keyword_contains",     # ← 与 §8.1 唯一算子相反
         "args": {"path": "answer", "keywords": ["内部编号"]}}])
    nc.case_type = "factuality"
    db.add(nc)
    env.cases.append(nc)
    await db.flush()

    sig = make_run(ch["agent"].id, ch["suite"].id)
    db.add(sig); await db.flush(); env.runs.append(sig)
    await db.commit()   # 见用例②：不 commit 会在 agent 父行持 S 锁，跨 session 求 X 锁即超时

    async with SessionLocal() as s:
        assert await _lock_agent(s, ch["agent"].id)
        run_id, overflow = await _create_error_regression_run_locked(
            s, agent_id=ch["agent"].id, suite_id=ch["suite"].id,
            version="it260-v2", signal_run_id=sig.id, cap=10)
    assert run_id is not None and overflow == [], "建单器未产出 run（夹具前提不成立）"

    run = (await db.execute(select(EvalRun).where(EvalRun.id == run_id))).scalars().one()
    env.runs.append(run)
    # 反假绿：建单计划数必须**含**那条形态不符的 case，否则本用例退化成用例②
    assert run.total_case == 3, "建单计划数未含形态不符 case ⇒ 本用例无判别力"
    assert run.case_ids and nc.id in run.case_ids
    now = _utcnow()
    run.status = "pending"
    run.lease_until = now - timedelta(seconds=60)
    await db.commit()
    await _make_result(db, run.id, ch["cases"][0], pass_fail="pass")   # 见用例②：须预置行
    await db.commit()

    cancelled = _patch_cancel(monkeypatch)
    assert await _reap_one_pass() == 1
    assert run.id in cancelled

    r = await _get_run(run.id)
    assert r.status == "timeout"
    assert r.error_case == 0
    ers = await _results_of(run.id)
    by_case = {e.case_id: e for e in ers}
    assert by_case[ch["cases"][0].id].pass_fail == "pass"   # 短路生效（未被 salvage 重算）
    assert by_case[ch["cases"][1].id].pass_fail == "na"     # 加载集内未完成 case 回填
    assert by_case[ch["cases"][1].id].error_type == "scheduler_unexecuted"
    # ★ 形态不符 case 不得产结果行（补行若照计划集遍历 ⇒ 此处多一行）
    assert nc.id not in by_case, "形态不符 case 被回填了结果行 ⇒ 补行遍历的不是加载集"
    assert len(ers) == 2, f"结果行数应为 2（加载集大小），实为 {len(ers)}"
    assert r.total_case == 2, "收尾未把 total_case 归一到结果数"


# ---------------- ③已终值行不被改写（独立断言） ----------------

@pytest.mark.asyncio(loop_scope="session")
async def test_reap_error_run_does_not_rewrite_terminal_result(db, env, monkeypatch):
    """回收 error run 不得改写已终值行：预置 fail 行回收后仍为 fail、且无 error 行混入。"""
    ch = await _seed_error_chain(env, db, ["it256-tr-a", "it256-tr-b"])
    now = _utcnow()
    run = _make_error_run(ch, total_case=2)
    run.status = "running"
    run.lease_until = now - timedelta(seconds=60)
    run.hard_deadline = now + timedelta(seconds=3600)
    db.add(run); await db.flush(); env.runs.append(run)
    await _make_result(db, run.id, ch["cases"][0], pass_fail="fail")   # 已判 fail
    await db.commit()

    _patch_cancel(monkeypatch)
    await _reap_one_pass()

    ers = await _results_of(run.id)
    by_case = {e.case_id: e for e in ers}
    assert by_case[ch["cases"][0].id].pass_fail == "fail"   # ★ 终值未被重算改写
    assert "error" not in {e.pass_fail for e in ers}        # §3.3：error run 取值域不含 'error'
    r = await _get_run(run.id)
    assert r.error_case == 0
