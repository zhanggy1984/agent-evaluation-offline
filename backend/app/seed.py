"""seed 数据：7 维度 / 内置断言算子 / metric_def / admin 用户。

用法：python -m app.seed   （在 backend/ 目录，需已跑过迁移）
幂等：已存在则跳过。admin 密码取 env ADMIN_PASSWORD（凭证 env-only，7.6 A3 后日志不回显明文，
缺显式配置直接失败——随机生成的密码不可恢复，会产生无法登录的 admin）。
"""
import asyncio
import logging
import os
import secrets
import sys

from sqlalchemy import select

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.core.config import settings  # noqa: E402
from app.core.db import SessionLocal, engine  # noqa: E402
from app.core.security import hash_password  # noqa: E402
from app.models import (  # noqa: E402
    AssertionOpDef, Dimension, MetricDef, User,
)
from app.core.constants import (  # noqa: E402
    ASSERTION_OP_CLASS, DIMENSION_CATEGORY, DIMENSION_METRIC_CLASS,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("seed")

DIMENSION_NAME = {
    "completeness": "完成度",
    "factuality": "事实性",
    "reasoning_quality": "思考链",
    "tool_usage": "工具使用",
    "ttft": "首字延迟",
    "e2e": "端到端延迟",
    "token_cost": "Token 成本",
}


async def _seed_dimensions(db) -> None:
    for code, category in DIMENSION_CATEGORY.items():
        exists = (await db.execute(select(Dimension.id).where(Dimension.code == code))).first()
        if exists:
            continue
        db.add(Dimension(code=code, name=DIMENSION_NAME.get(code, code), category=category))
        logger.info("seed dimension %s", code)


async def _seed_assertion_ops(db) -> None:
    for op, class_path in ASSERTION_OP_CLASS.items():
        exists = (await db.execute(select(AssertionOpDef.id).where(AssertionOpDef.op == op))).first()
        if exists:
            continue
        db.add(AssertionOpDef(op=op, class_path=class_path))
        logger.info("seed assertion_op %s", op)


async def _seed_metric_defs(db) -> None:
    for dim, class_path in DIMENSION_METRIC_CLASS.items():
        exists = (
            await db.execute(select(MetricDef.id).where(MetricDef.dimension_code == dim))
        ).first()
        if exists:
            continue
        db.add(MetricDef(dimension_code=dim, class_path=class_path))
        logger.info("seed metric_def %s", dim)


async def _seed_judge_rubrics(db) -> None:
    """内置 judge rubric 幂等插入（UK: dimension_code+interface_id=0+version）。"""
    from app.judge.rubric import BUILTIN_RUBRICS
    from app.models import JudgeRubric
    for dim, entry in BUILTIN_RUBRICS.items():
        exists = (await db.execute(select(JudgeRubric.id).where(
            JudgeRubric.dimension_code == dim,
            JudgeRubric.interface_id == 0,
            JudgeRubric.version == entry["version"]))).first()
        if exists:
            continue
        db.add(JudgeRubric(dimension_code=dim, interface_id=0, version=entry["version"],
                           template=entry["template"]))
        logger.info("seed judge_rubric %s v%s", dim, entry["version"])


async def _seed_agents(db) -> None:
    """四家自研 agent 幂等 seed（含 interface / Fernet 凭证 / 默认权重 / 示例 suite+case）。

    adapter_config 单一来源：app/seed_data.py（verify_*_e2e.py 亦从此 import）。
    唯一键 = Agent.name（与 POST /api/agents 查重一致）；已存在则整家跳过。
    """
    import json as _json
    from app.core.constants import DEFAULT_WEIGHTS
    from app.core.security import fernet_encrypt
    from app.models import (
        Agent, AgentDimensionWeight, AgentInterface, CaseScene, SceneCatalog, TestCase, TestSuite,
    )
    from app.seed_data import SEED_AGENTS

    for spec in SEED_AGENTS:
        exists = (await db.execute(select(Agent.id).where(Agent.name == spec["name"]))).first()
        if exists:
            logger.info("seed agent %s 已存在，跳过", spec["name"])
            continue
        agent = Agent(
            name=spec["name"], base_url=spec["base_url"],
            adapter_type=spec["adapter_type"], adapter_config=spec["adapter_config"],
            contract_version=spec["contract_version"],
        )
        secrets_ = spec.get("auth_secrets")
        if secrets_:
            agent.auth_config = fernet_encrypt(
                _json.dumps(secrets_, ensure_ascii=False).encode("utf-8"))
        db.add(agent)
        await db.flush()
        # agent 级默认权重（与 POST /api/agents 的 _init_default_weights 同口径）
        for dim, weight in DEFAULT_WEIGHTS.items():
            db.add(AgentDimensionWeight(agent_id=agent.id, interface_id=0,
                                        dimension_code=dim, weight=weight))
        # 接口（name → id 映射供 case 关联）
        iface_ids: dict[str, int] = {}
        for iface in spec["interfaces"]:
            ai = AgentInterface(agent_id=agent.id, name=iface["name"], path=iface["path"],
                                method=iface["method"], contract_type=iface["contract_type"],
                                contract_version=iface["contract_version"])
            db.add(ai)
            await db.flush()
            iface_ids[iface["name"]] = ai.id
        # 场景清单（离线镜像；存量库不回填——活源在 agent 标准端点，脚手架 discover→sync 补）
        for sc in spec.get("scenes", []):
            db.add(SceneCatalog(agent_id=agent.id, scene_tag=sc["tag"],
                                description=sc.get("description", "")))
        # 示例 suite + case
        suite = spec["sample_suite"]
        ss = TestSuite(agent_id=agent.id, name=suite["name"], description=suite.get("description"))
        db.add(ss)
        await db.flush()
        for c in suite["cases"]:
            tc = TestCase(
                suite_id=ss.id, interface_id=iface_ids[c["interface"]], name=c["name"],
                description=c.get("description", ""), input_type=c["input_type"],
                input=c["input"], file_ref=c.get("file_ref"),
                expected=c.get("expected", {}), assertions=c["assertions"],
                metrics=c["metrics"], status="active",
                # 阶段 7 空库验证发现：is_gold/is_held_out 未透传 → gold 用例从未落库
                is_gold=c.get("is_gold", False), is_held_out=c.get("is_held_out", False))
            db.add(tc)
            await db.flush()
            for tag in c.get("scenes", []):
                db.add(CaseScene(case_id=tc.id, scene_tag=tag))
        logger.info("seed agent %s（interface %d 个，case %d 条）",
                    spec["name"], len(spec["interfaces"]), len(suite["cases"]))


async def _backfill_gold_scores(db) -> None:
    """6.4 幂等补齐存量 is_gold 用例的 expected.judge_gold_scores（漂移检测数据源）。

    数据来源 = seed_data 同名 is_gold 用例定义；仅补齐缺失（已有人工值不覆盖）。
    存量库手工标 is_gold 的用例也能拿到金标准判分依据。
    """
    from app.models import Agent, TestCase, TestSuite
    from app.seed_data import SEED_AGENTS

    gold_defs: dict[tuple[str, str], dict] = {}
    for spec in SEED_AGENTS:
        for c in spec.get("sample_suite", {}).get("cases", []):
            if c.get("is_gold") and c.get("expected", {}).get("judge_gold_scores"):
                gold_defs[(spec["name"], c["name"])] = c
    if not gold_defs:
        return
    rows = (await db.execute(
        select(TestCase, Agent.name).select_from(TestCase)
        .join(TestSuite, TestCase.suite_id == TestSuite.id)
        .join(Agent, TestSuite.agent_id == Agent.id)
        .where(TestCase.is_gold == True))).all()
    updated = 0
    for tc, agent_name in rows:
        cdef = gold_defs.get((agent_name, tc.name))
        if cdef is None:
            continue
        expected = dict(tc.expected or {})
        if "judge_gold_scores" in expected:
            continue
        expected["judge_gold_scores"] = cdef["expected"]["judge_gold_scores"]
        tc.expected = expected
        updated += 1
        logger.info("backfill judge_gold_scores case=%s（%s）", tc.id, agent_name)
    if updated:
        await db.flush()


async def _seed_baseline_targets(db) -> None:
    """6.3 默认达标分：seed 四家 agent accuracy 四维 agent 级默认（interface_id=0 哨兵）。

    幂等（联合主键 agent+interface+dimension）；已存在不覆盖（保留人工标定）。
    初始值 70（百分制），approval_status=auto（手动改才进双签流程）。
    """
    from app.core.constants import ACCURACY_DIMENSIONS
    from app.models import Agent, BaselineTarget
    from app.seed_data import SEED_AGENTS

    for spec in SEED_AGENTS:
        agent_id = (await db.execute(select(Agent.id).where(
            Agent.name == spec["name"]))).scalar_one_or_none()
        if agent_id is None:
            continue
        for dim in ACCURACY_DIMENSIONS:
            exists = (await db.execute(select(BaselineTarget).where(
                BaselineTarget.agent_id == agent_id,
                BaselineTarget.interface_id == 0,
                BaselineTarget.dimension_code == dim))).first()
            if exists:
                continue
            db.add(BaselineTarget(agent_id=agent_id, interface_id=0, dimension_code=dim,
                                  target_score=70, calibration_source="手动",
                                  approval_status="auto"))
            logger.info("seed baseline_target agent=%s dim=%s target=70", spec["name"], dim)


async def _seed_admin(db) -> None:
    exists = (await db.execute(select(User.id).where(User.username == "admin"))).first()
    if exists:
        return
    # 7.6 A3：凭证 env-only，日志不回显明文。缺显式配置拒绝 seed（fail-fast），
    # 避免随机生成不可恢复的密码 → 产生无法登录的 admin 账号。
    password = os.environ.get("ADMIN_PASSWORD")
    if not password:
        raise RuntimeError("seed admin 需显式设置 ADMIN_PASSWORD（凭证不进日志，容器 .env 已配置）")
    db.add(User(
        username="admin",
        password_hash=hash_password(password),
        role="admin",
        password_changed_at=None,  # 首登强制改密
    ))
    logger.warning("admin 初始密码已写入（来源 ADMIN_PASSWORD env，首登强制改密）")


async def _seed_model_prices(db) -> None:
    """模型单价幂等 seed（阶段 7 前置）：按主键 (model, effective_from) 查重。

    成本计算数据源——verify_scoring.py 曾手动插 mock-model，现固化 DeepSeek 版本化价格演进。
    """
    from app.models import ModelPrice
    from app.seed_data import SEED_MODEL_PRICES

    for p in SEED_MODEL_PRICES:
        exists = (await db.execute(select(ModelPrice).where(
            ModelPrice.model == p["model"],
            ModelPrice.effective_from == p["effective_from"]))).first()
        if exists:
            continue
        db.add(ModelPrice(model=p["model"], input_price=p["input_price"],
                          output_price=p["output_price"], effective_from=p["effective_from"]))
        logger.info("seed model_price %s @ %s", p["model"], p["effective_from"].date())


async def _seed_users(db) -> None:
    """走查/演示用户幂等 seed（evaluator/viewer）。

    P0 安全收敛：密码改由 env DEMO_EVALUATOR_PASSWORD / DEMO_VIEWER_PASSWORD 注入
    （对齐 _seed_admin 的 env-only 模式），缺 env 则跳过不建——生产默认无已知口令账号；
    password_changed_at=None → 首登强制改密（与 admin 同口径）。
    """
    from app.seed_data import SEED_USERS

    for u in SEED_USERS:
        exists = (await db.execute(select(User.id).where(User.username == u["username"]))).first()
        if exists:
            continue
        # P0 安全收敛：密码只从 env 注入（DEMO_<用户名大写>_PASSWORD），缺 env 跳过不建
        password = os.environ.get(f"DEMO_{u['username'].upper()}_PASSWORD")
        if not password:
            logger.info("seed user %s 缺 DEMO_*_PASSWORD env，跳过（生产不建 demo 账号）",
                        u["username"])
            continue
        db.add(User(username=u["username"], password_hash=hash_password(password),
                    role=u["role"], password_changed_at=None))
        logger.info("seed user %s（%s）", u["username"], u["role"])


# §七 配置项清单默认值（scope/is_hot 与详设一致）
DEFAULT_SYSTEM_CONFIG = {
    # 运行期（run scope：仅 run 创建时快照到 run_config）
    # P2-C2：meta 为配置契约（type/min/max/nullable/max_len/item_type/max_items），
    # 配置中心 put 时校验（app/core/sysconfig_schema.py），新增 key 必须带 meta。
    "global_max_inflight": {"value": 16, "scope": "run", "is_hot": True,
                            "meta": {"type": "int", "min": 1, "max": 1024}},
    "per_agent_concurrency": {"value": 3, "scope": "run", "is_hot": True,
                              "meta": {"type": "int", "min": 1, "max": 1024}},
    "case_timeout": {"value": 120, "scope": "run", "is_hot": True,
                     "meta": {"type": "int", "min": 1, "max": 86400}},
    "scoring_timeout": {"value": 3600, "scope": "run", "is_hot": True,   # scoring 超时（秒）：超时 → scoring_failed 兜底
                        "meta": {"type": "int", "min": 1, "max": 604800}},
    "contract_check_timeout": {"value": 300, "scope": "run", "is_hot": True,
                               "meta": {"type": "int", "min": 1, "max": 86400}},
    "sse_idle_timeout": {"value": 60, "scope": "run", "is_hot": True,
                         "meta": {"type": "int", "min": 1, "max": 86400}},
    "run_timeout": {"value": None, "scope": "run", "is_hot": True,   # None → orchestrator.estimate_run_timeout 估算（7.4）
                    "meta": {"type": "int", "min": 1, "max": 604800, "nullable": True}},
    "perf_repeat_count": {"value": 5, "scope": "run", "is_hot": True,
                          "meta": {"type": "int", "min": 1, "max": 100}},
    "judge_concurrency": {"value": 4, "scope": "run", "is_hot": True,
                          "meta": {"type": "int", "min": 1, "max": 64}},
    "judge_call_timeout": {"value": 120, "scope": "run", "is_hot": True,
                           "meta": {"type": "int", "min": 1, "max": 3600}},
    "judge_na_threshold": {"value": 0.3, "scope": "run", "is_hot": True,
                           "meta": {"type": "number", "min": 0.0, "max": 1.0}},
    "error_rate_block": {"value": 0.1, "scope": "run", "is_hot": True,
                         "meta": {"type": "number", "min": 0.0, "max": 1.0}},
    "assertion_penalty": {"value": 30, "scope": "run", "is_hot": True,
                          "meta": {"type": "int", "min": 0, "max": 100}},
    "judge_max_retries": {"value": 2, "scope": "run", "is_hot": True,
                          "meta": {"type": "int", "min": 0, "max": 10}},
    "judge_repeat": {"value": 2, "scope": "run", "is_hot": True,
                     "meta": {"type": "int", "min": 1, "max": 10}},
    "breaker_failure_threshold": {"value": 5, "scope": "run", "is_hot": True,
                                  "meta": {"type": "int", "min": 1, "max": 1000}},
    "breaker_open_duration": {"value": 60, "scope": "run", "is_hot": True,
                              "meta": {"type": "int", "min": 1, "max": 86400}},
    "breaker_half_open_probe": {"value": 1, "scope": "run", "is_hot": True,
                                "meta": {"type": "int", "min": 1, "max": 100}},
    "retry_backoff_max": {"value": 10, "scope": "run", "is_hot": True,
                          "meta": {"type": "int", "min": 1, "max": 3600}},
    "max_retries": {"value": 1, "scope": "run", "is_hot": True,
                    "meta": {"type": "int", "min": 0, "max": 100}},
    # 进程级（global）
    "retain_runs": {"value": 50, "scope": "global", "is_hot": True,
                    "meta": {"type": "int", "min": 1, "max": 10000}},
    "heartbeat_interval": {"value": 30, "scope": "global", "is_hot": False,
                           "meta": {"type": "int", "min": 1, "max": 86400}},
    "judge_llm.base_url": {"value": "", "scope": "global", "is_hot": True,
                           "meta": {"type": "str", "max_len": 256}},
    "judge_llm.model_name": {"value": "", "scope": "global", "is_hot": True,
                             "meta": {"type": "str", "max_len": 128}},
    # P0 补齐：judge 出站域名白名单（SSRF §15.3）。缺失时 worker 读空 → _validate_allowlist
    # 拒绝所有 host（语义维度永远判不出分）且配置中心因"未知 key"无法补救，必须 seed 默认值。
    # 预设常用 OpenAI 兼容厂商（对齐 solution_detail §七配置表），按实际选用在配置中心增删。
    "llm_allowlist": {
        "value": ["api.deepseek.com", "dashscope.aliyuncs.com", "open.bigmodel.cn", "api.moonshot.cn"],
        "scope": "global", "is_hot": True,
        "meta": {"type": "list", "item_type": "str", "max_items": 50},
    },
    # 注册期（registration：仅 agent 注册/文件上传时校验）
    "file_max_size": {"value": 50, "scope": "registration", "is_hot": True,
                      "meta": {"type": "int", "min": 1, "max": 10240}},
    # 7.6 A4 补漏：169.254.0.0/16（云元数据段）必须与代码常量 DEFAULT_AGENT_CIDRS 一致剔除，
    # 否则 DB 配置残留会绕过 create_agent 的 SSRF 校验（_get_allowlist_cidrs 读的是本配置）
    "base_url_allowlist": {
        "value": ["127.0.0.0/8", "10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16"],
        "scope": "registration", "is_hot": False,
        "meta": {"type": "list", "item_type": "str", "max_items": 50},
    },
}


async def _seed_system_config(db) -> None:
    from app.models import SystemConfig
    for key, cfg in DEFAULT_SYSTEM_CONFIG.items():
        exists = await db.get(SystemConfig, key)
        if exists:
            continue
        db.add(SystemConfig(key=key, value=cfg["value"], scope=cfg["scope"], is_hot=cfg["is_hot"]))
        logger.info("seed system_config %s", key)


async def seed() -> None:
    async with SessionLocal() as db:
        await _seed_dimensions(db)
        await _seed_assertion_ops(db)
        await _seed_metric_defs(db)
        await _seed_judge_rubrics(db)
        await _seed_system_config(db)
        await _seed_model_prices(db)
        await _seed_agents(db)
        await _backfill_gold_scores(db)
        await _seed_baseline_targets(db)
        await _seed_admin(db)
        await _seed_users(db)
        await db.commit()
    logger.info("seed 完成")


async def _main() -> None:
    """seed 后在同一事件循环内释放连接池（asyncio.run 结束后 loop 已关闭，无法再 dispose）。"""
    try:
        await seed()
    finally:
        await engine.dispose()


if __name__ == "__main__":
    if settings.app_env == "test":
        logger.error("test 环境不需要 seed（sqlite 内存）")
        sys.exit(1)
    asyncio.run(_main())
