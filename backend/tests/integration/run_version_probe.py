"""批 C8 真库验收：§7.5 项 1 —— version 由 strict semver 放宽为白名单字符集域校验。

跑法（宿主直连共享库，env 从容器整体导出后覆写，见 memory `offline-real-db-probe-recipe`）：
    .venv\\Scripts\\python.exe tests\\integration\\run_version_probe.py

**真的部分**：真库、真 `create_run` 处理函数全链（真 pydantic 解析、真 `_VERSION_RE` 校验、
真写 run 行）。
**桩的部分**：`orchestrator.start_run`（不真跑执行）与 `write_audit`（不落审计行）——
本批验的是「收不收 / 拒没拒」，不是「跑得对不对」。

**判据为什么要回查库、而不能只看返回值**：
- 放宽侧：`create_run` 返 200 只证明**校验点放行**，不证明**落库的 version 就是原值**
  （若某处中途重置/截断，单看状态码仍是绿的）。故必须回查 `eval_run.version` 逐字相等。
- 收紧侧：返 400 只证明**处理函数拒绝了**，不证明**没有半途写进去一行**（拒绝点必须在
  `session.add` 之前）。故必须回查库中该 version 的行数为 0。

**本探针证不了什么**（显式声明，勿外推）：
- **前端转义层**：本批把安全压力更多压到 `Dashboard.vue:843` 的 `esc()` 上，那是前端行为，
  本探针不覆盖（全前端零 `v-html` 是本批成立的前提，已在 `runs.py` 注释里写明）；
- **其他写入面的 version 安全**：只覆盖 `create_run` 一条路径（`rerun_run` 无 version 入参）；
- **存量 version 数据**：只做本探针自造行的断言，不扫描、不订正全库存量。
"""
import asyncio
import logging
import sys
from datetime import datetime, timezone
from types import SimpleNamespace

from sqlalchemy import func, select

from app.api import runs as runs_api
from app.api.runs import RunCreate
from app.core.db import SessionLocal
from app.core.errors import ApiError
from app.models import Agent, AgentInterface, EvalRun, TestCase, TestSuite

# 带 tz：同目录既有探针用的是 naive `datetime.now()`（ruff DTZ005 存量），本文件是新增、
# 无基线豁免，顺手写对——它只当唯一戳用，时区语义无所谓。
STAMP = datetime.now(timezone.utc).strftime("%H%M%S")

PASS_COUNT = 0
FAIL_COUNT = 0

USER = SimpleNamespace(id=1, role="admin", username="probe-c8")

# 放宽侧：非 semver 的真实版本形态，**必须放行且原值落库**。
# 每个形态各用独立 agent，避免上一条 pending run 占槽导致 409 串场。
ACCEPT_CASES = (
    ("v-prefix", "v1.2.3"),
    ("prerelease", "1.2.3-beta.1"),
    ("date-style", "2026.09.15"),
    ("build-meta", "1.2.3+build.5"),
    ("two-segment", "1.2"),
)

# 收紧侧：任何能构成 HTML 注入的形态都必须拒（P2-C4 语义，放宽不得触及）。
REJECT_CASES = (
    ("html-suffix", "1.2.3<script>"),
    ("html-full", "<script>alert(1)</script>"),
    ("with-space-tag", "1.2.3 <img src=x onerror=alert(1)>"),
    ("quote", "1.2.3'"),
)


def _check(name: str, got, want) -> None:
    global PASS_COUNT, FAIL_COUNT
    if got == want:
        PASS_COUNT += 1
        print(f"  PASS  {name}")
    else:
        FAIL_COUNT += 1
        print(f"  FAIL  {name}: got={got!r} want={want!r}")


async def _stub(*_a, **_kw):
    return None


# 探针内替换执行与审计（模块级）；被 create_run 直接引用
runs_api.orchestrator.start_run = _stub
runs_api.write_audit = _stub


async def _ensure_agent(name: str) -> tuple[int, int]:
    """幂等建 agent + 普通 suite + 1 条 active 普通 case（case_type=NULL，过 V1 空转校验）。"""
    async with SessionLocal() as s:
        agent = (await s.scalars(select(Agent).where(Agent.name == name))).first()
        if agent is None:
            agent = Agent(name=name, base_url="http://mock.local", adapter_type="config",
                          adapter_config={}, enabled=True, contract_version="1.0")
            s.add(agent)
            await s.flush()
        iface = (await s.scalars(select(AgentInterface).where(
                AgentInterface.agent_id == agent.id,
                AgentInterface.name == "probe-c8-iface"))).first()
        if iface is None:
            iface = AgentInterface(agent_id=agent.id, name="probe-c8-iface", path="/v1/chat",
                                   method="POST", contract_type="sse", contract_version="1.0",
                                   enabled=True)
            s.add(iface)
            await s.flush()
        suite = (await s.scalars(select(TestSuite).where(
            TestSuite.agent_id == agent.id, TestSuite.name == f"{name}-suite"))).first()
        if suite is None:
            suite = TestSuite(agent_id=agent.id, name=f"{name}-suite", is_error_suite=False)
            s.add(suite)
            await s.flush()
        case = (await s.scalars(select(TestCase).where(
                TestCase.suite_id == suite.id, TestCase.name == f"{name}-case"))).first()
        if case is None:
            case = TestCase(suite_id=suite.id, interface_id=iface.id, name=f"{name}-case",
                            input_type="text", input="你好", expected={"answer": "你好"},
                            metrics=["accuracy"], case_type=None, is_gold=False, status="active")
            s.add(case)
            await s.flush()
        ids = (agent.id, suite.id)
        await s.commit()
        return ids


async def _create(agent_id: int, suite_id: int, version: str):
    """真调 create_run，返回 ('ok', None) 或 ('err', status_code, message)。"""
    body = RunCreate(agent_id=agent_id, suite_id=suite_id, version=version,
                     trigger_type="manual", case_ids=None)
    # 会话走 app 自己的 SessionLocal（expire_on_commit=False）：自建 AsyncSession 默认
    # expire_on_commit=True，handler 内 commit 后取属性会触发惰性刷新 → MissingGreenlet。
    async with SessionLocal() as s:
        try:
            await runs_api.create_run(body, USER, s)
        except ApiError as e:
            return ("err", e.status_code, e.message)
    return ("ok", None)


async def _db_versions(agent_id: int, version: str) -> list[str]:
    """回查库中该 agent 下 version 完全相等的行（字符串逐字比对，不做归一化）。"""
    async with SessionLocal() as s:
        return list((await s.scalars(select(EvalRun.version).where(
            EvalRun.agent_id == agent_id, EvalRun.version == version))).all())


async def _finish(agent_id: int, version: str) -> None:
    """把本次建出的 run 置终态，**只为让探针可重复**。

    `orchestrator.start_run` 在本探针里是桩 ⇒ run 永久停在 `pending` ⇒ 占住 §7.5 项 2 的
    活跃槽位（`manual` 占槽仍 409），同一 agent 第二轮必被 409 挡下 —— 本探针首轮就踩了：
    第二轮起放宽侧整片红，但那是**探针不可重复**，不是实现红。
    只改 status，不碰 version ⇒ 上面的「逐字相等」判据不受影响。
    """
    async with SessionLocal() as s:
        run = (await s.scalars(select(EvalRun).where(
            EvalRun.agent_id == agent_id, EvalRun.version == version))).first()
        if run is not None:
            run.status = "completed"
            await s.commit()


async def scenario_accept() -> None:
    print("\n[场景 1-5] 放宽侧：非 semver 形态必须放行，且**原值落库**")
    for tag, base in ACCEPT_CASES:
        # **唯一化是必需的**：`_ensure_agent` 按 name 幂等复用 agent，回查又是按
        # (agent_id, version) 相等 ⇒ 不带 STAMP 时**上一轮跑留下的行会被这一轮命中**，
        # 配对断言恒绿（本探针首轮就踩了这个：换回旧 `_SEMVER` 做判别力实测时，配对断言
        # 没红而建单断言红了，才暴露出来）。加 STAMP 后回查命中的必然是**本次**建的行。
        version = f"{base}-{STAMP}"
        a, suite = await _ensure_agent(f"probe-c8-ok-{tag}")
        got = await _create(a, suite, version)
        _check(f"1 · {tag} ({version!r}) → 建单放行", got[0], "ok")
        rows = await _db_versions(a, version)
        # 逐字相等，不是「非空」——非空断言在 version 被改动时仍会绿。
        _check(f"1 · {tag} → 库中 version 逐字为 {version!r}", rows, [version])
        await _finish(a, version)   # 释放活跃槽位，让本探针可重复（见函数 docstring）


async def scenario_reject() -> None:
    print("\n[场景 6-9] 收紧侧：HTML 注入形态必须拒，且**库里不留行**")
    # **这里故意不加 STAMP**（与放宽侧相反）：判据是「库中 0 行」，固定 version 让
    # 「一旦某轮真落了行」成为**此后每轮都可见**的污点；若也加 STAMP，「0 行」几乎恒真，
    # 判别力反而更弱。形态原样固定，也保证拒绝理由（含 `<`/含空白/首字符）不被前缀改写。
    for tag, version in REJECT_CASES:
        a, suite = await _ensure_agent(f"probe-c8-no-{tag}")
        got = await _create(a, suite, version)
        _check(f"2 · {tag} → 建单拒绝 400", got[0] == "err" and got[1], 400)
        rows = await _db_versions(a, version)
        # 拒绝点必须在 session.add 之前；否则「拒了」但脏行已落库。
        _check(f"2 · {tag} → 库中该 version 行数为 0", len(rows), 0)
        async with SessionLocal() as s:
            n = (await s.execute(select(func.count()).select_from(EvalRun).where(
                EvalRun.agent_id == a))).scalar() or 0
        _check(f"2 · {tag} → 该 agent 下一条 run 都没建", n, 0)


async def _release_stale() -> None:
    """清掉**本探针自造 agent** 下残留的活跃 run（早期版本跑剩的 pending）。

    范围严格限定 `probe-c8-%`，不碰任何其他 agent。**不删行**（version 落库事实保留），
    只置终态；否则残留会 409 挡住后续每一轮。
    """
    async with SessionLocal() as s:
        runs = (await s.scalars(select(EvalRun).join(
            Agent, Agent.id == EvalRun.agent_id).where(
            Agent.name.like("probe-c8-%"), EvalRun.agent_id == Agent.id,
            EvalRun.status.in_(("pending", "running"))))).all()
        for run in runs:
            run.status = "completed"
        await s.commit()
        if runs:
            print(f"  INFO  清理本探针历史活跃 run {len(runs)} 条（置终态，不删行）")


async def main() -> None:
    logging.basicConfig(level=logging.ERROR)   # 探针自身打 400 日志，压掉噪声
    await _release_stale()
    await scenario_accept()
    await scenario_reject()
    print(f"\n===== {PASS_COUNT} passed / {FAIL_COUNT} failed =====")
    sys.exit(1 if FAIL_COUNT else 0)


if __name__ == "__main__":
    asyncio.run(main())
