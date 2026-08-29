"""7.2 集成测试数据辅助：it72-* 临时数据构造与清理（容器内真库）。

独立模块供测试文件 import（pytest 目录 prepend 模式，同目录 `import helpers` 即可）。
cleanup 用独立新 session（函数内延迟 import），与测试所用 session 解耦：
测试中途异常（flush 失败等）会让测试 session 进 pending rollback / 失效，
若复用该 session 清理会再次失败留下残留。
宿主无 aiomysql 时本模块不被 import（conftest importorskip 先拦截），故函数内可安全 import app.core.db。
"""

from sqlalchemy import delete

from app.models import (
    Agent,
    AgentInterface,
    CaseScene,
    CaseVersion,
    EvalResult,
    EvalRun,
    SceneCatalog,
    TestCase,
    TestSuite,
)


class Env:
    """本次测试创建的 it72-* 数据；cleanup() 按 FK 依赖逆序删除。"""

    def __init__(self):
        self.agents: list[Agent] = []
        self.interfaces: list[AgentInterface] = []
        self.suites: list[TestSuite] = []
        self.cases: list[TestCase] = []
        self.runs: list[EvalRun] = []

    async def cleanup(self) -> None:
        """逆序删：judge_task → eval_result → case_version → run → case → interface → suite → agent。

        独立 session 执行，不受测试 session 状态影响。
        """
        from app.core.db import SessionLocal
        from app.models import JudgeTask

        async with SessionLocal() as db:
            if self.runs:
                await db.execute(delete(JudgeTask).where(JudgeTask.run_id.in_(r.id for r in self.runs)))
                await db.execute(delete(EvalResult).where(EvalResult.run_id.in_(r.id for r in self.runs)))
            if self.cases:
                # case_scene FK→test_case.id，须先于 case 删除
                await db.execute(delete(CaseScene).where(CaseScene.case_id.in_(c.id for c in self.cases)))
                await db.execute(delete(CaseVersion).where(CaseVersion.case_id.in_(c.id for c in self.cases)))
            if self.runs:
                await db.execute(delete(EvalRun).where(EvalRun.id.in_(r.id for r in self.runs)))
            if self.cases:
                await db.execute(delete(TestCase).where(TestCase.id.in_(c.id for c in self.cases)))
            if self.interfaces:
                await db.execute(delete(AgentInterface).where(AgentInterface.id.in_(i.id for i in self.interfaces)))
            if self.suites:
                await db.execute(delete(TestSuite).where(TestSuite.id.in_(s.id for s in self.suites)))
            if self.agents:
                # scene_catalog FK→agent.id，须先于 agent 删除
                await db.execute(delete(SceneCatalog).where(SceneCatalog.agent_id.in_(a.id for a in self.agents)))
                await db.execute(delete(Agent).where(Agent.id.in_(a.id for a in self.agents)))
            await db.commit()


def make_agent(name: str = "it72-orch") -> Agent:
    """临时 agent：adapter_config 空（探测/执行全 mock，不依赖真实接入）。"""
    return Agent(name=name, base_url="http://mock.local", adapter_type="config",
                 adapter_config={}, contract_version="1.0", enabled=True)


def make_interface(agent_id: int, name: str = "it72-iface") -> AgentInterface:
    return AgentInterface(agent_id=agent_id, name=name, path="/v1/chat",
                          method="POST", contract_type="sse",
                          contract_version="1.0", enabled=True)


def make_suite(agent_id: int, name: str = "it72-suite") -> TestSuite:
    return TestSuite(agent_id=agent_id, name=name)


def make_case(suite_id: int, interface_id: int, name: str,
              assertions: list | None = None,
              metrics: dict | None = None,
              expected: dict | None = None,
              is_gold: bool = False) -> TestCase:
    """text 型 case。默认 completeness 断言（field_nonempty answer，评分可算分）。"""
    return TestCase(
        suite_id=suite_id, interface_id=interface_id, name=name,
        input_type="text",
        input={"content": "测试输入", "params": {}},
        expected=expected or {},
        assertions=assertions if assertions is not None else [
            {"dimension": "completeness", "op": "field_nonempty", "args": {"path": "answer"}},
        ],
        metrics=metrics if metrics is not None else {"completeness": {"enabled": True}},
        status="active", is_gold=is_gold,
    )


def make_run(agent_id: int, suite_id: int, run_config: dict | None = None) -> EvalRun:
    """manual run（非留出集，跑 suite 全部 active case）。"""
    return EvalRun(agent_id=agent_id, suite_id=suite_id, version="1.0",
                   trigger_type="manual", status="pending", generation=1,
                   run_config=run_config or {})


async def create_chain(db, *, suite_name="it72-suite", iface_name="it72-iface",
                       case_names=None, case_kw=None) -> dict:
    """一次性造 agent → interface → suite → cases（逐层 flush，保证 FK id 可用）。

    case_kw 透传给 make_case（metrics/expected/is_gold 等）；case_names 缺省 1 个。
    返回 {"agent", "iface", "suite", "cases"}（调用方负责 commit + 记入 env）。
    """
    agent = make_agent(); db.add(agent); await db.flush()
    iface = make_interface(agent.id, iface_name); db.add(iface); await db.flush()
    suite = make_suite(agent.id, suite_name); db.add(suite); await db.flush()
    names = case_names if case_names is not None else ["it72-case"]
    cases = []
    for n in names:
        c = make_case(suite.id, iface.id, n, **(case_kw or {}))
        db.add(c)
        cases.append(c)
    await db.flush()
    return {"agent": agent, "iface": iface, "suite": suite, "cases": cases}
