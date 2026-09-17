"""T-5.5 / G3 批 1 真库验收：ack 积压判据的正向 / 负对照 / 恢复 / 可重复性。

跑法（宿主直连共享库，env 从容器整体导出后覆写，见 memory `offline-real-db-probe-recipe`）：
    .venv\\Scripts\\python.exe tests\\integration\\ack_stale_probe_probe.py

**⚠️ 本探针会往真表写行**（`ai_evaluation.error_backflow_inbox`）——**勿引用固定水位，它会腐**：
造前当场取 `select count(*), sum(ack_status='acked') from error_backflow_inbox;`
（2026-09-17 复核 = **25 行、25/25 `acked`**；docstring 早期记的「12 行、12/12」已不准）。
按两条既往教训办：
- `probe-repeatability-and-unique-input`：判据是「**必须命中**」⇒ 入参**唯一化**
  （`probe-ackstale-<uuid>`），且断言用「含/不含」而非等号——库内若有别的 stale 行，
  等号会把它读成探针失败；
- `self-injected-fault-looks-like-real-defect`：**造前记基线水位、造完当场删、撤销后回读**。

**本探针证不了什么**（显式声明，勿外推）：
- **`_INTERVAL` 真的每 60s**：不真等，只验单轮语义；
- **webhook 真的发得出**：未配置 URL 时走的是降级分支；本探针只断言 `notify` **被调用**，
  真实 HTTP 出站无证据（通道 URL 属环境输入，见 T-5.5）；
- **多 worker 抢锁**：只验单进程单轮，`GET_LOCK` 互斥无真机证据。
"""
import asyncio
import logging
import sys
import uuid
from unittest import mock

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from app.core.config import Settings
from app.runner import ack_stale_probe as asp

PASS_COUNT = 0
FAIL_COUNT = 0
PREFIX = "probe-ackstale-"
STALE_MIN = asp._STALE_MINUTES + 1     # 比阈值多 1 分钟 ⇒ 必落入告警侧


def _check(name: str, got, want) -> None:
    global PASS_COUNT, FAIL_COUNT
    if got == want:
        PASS_COUNT += 1
        print(f"  PASS  {name}")
    else:
        FAIL_COUNT += 1
        print(f"  FAIL  {name}: got={got!r} want={want!r}")


def _check_true(name: str, got) -> None:
    _check(name, bool(got), True)


# ---- 造/删/查（全部走原生 SQL，`updated_at` 回拨才可控） --------------------

async def _mk(engine, pid: str, ack_status: str, minutes_ago: int) -> None:
    """造一行；`updated_at` 与 `received_at` 一并回拨到阈值之外。"""
    async with engine.begin() as conn:
        await conn.execute(text(
            "INSERT INTO error_backflow_inbox "
            "(payload_id, schema_version, case_type, envelope_json, status, ack_status,"
            " received_at, updated_at) "
            "VALUES (:pid, 'v1', 'error', '{}', 'case_created', :ack,"
            " NOW() - INTERVAL :m MINUTE, NOW() - INTERVAL :m MINUTE)"
        ), {"pid": pid, "ack": ack_status, "m": minutes_ago})


async def _set_ack(engine, pid: str, ack_status: str) -> None:
    async with engine.begin() as conn:
        await conn.execute(text(
            "UPDATE error_backflow_inbox SET ack_status = :ack WHERE payload_id = :pid"
        ), {"ack": ack_status, "pid": pid})


async def _rm_prefix(engine) -> None:
    async with engine.begin() as conn:
        await conn.execute(text(
            "DELETE FROM error_backflow_inbox WHERE payload_id LIKE :p"
        ), {"p": f"{PREFIX}%"})


async def _count_prefix(engine) -> int:
    async with engine.begin() as conn:
        return (await conn.execute(text(
            "SELECT COUNT(*) FROM error_backflow_inbox WHERE payload_id LIKE :p"
        ), {"p": f"{PREFIX}%"})).scalar()


async def _stale_ids(engine) -> set:
    """按判据**独立直查**库算期望集合（与 probe_once 同谓词、独立写法）。"""
    async with AsyncSession(engine) as s:
        rows = (await s.execute(text(
            "SELECT payload_id FROM error_backflow_inbox "
            "WHERE ack_status <> 'acked' "
            f"AND updated_at < NOW() - INTERVAL {asp._STALE_MINUTES} MINUTE"
        ))).scalars().all()
    return {str(r) for r in rows}


async def _run_probe(last_ids: set) -> tuple[set, list]:
    """跑一轮 `probe_once`，捕获发给 `notify` 的告警。"""
    sent: list = []

    async def _rec(level: str, title: str, detail: str) -> None:
        sent.append((level, title, detail))

    with mock.patch.object(asp, "notify", _rec):
        got = await asp.probe_once(last_ids)
    return got, sent


# ---- 场景 ------------------------------------------------------------------

async def scenario_1(engine) -> tuple[str, str]:
    """正向 + **负对照**：只差 `ack_status` 一列，一行触发、一行不触发。"""
    print("\n[场景 1] 正向命中 / 负对照不命中（唯一差别 = ack_status）")
    pos, neg = f"{PREFIX}{uuid.uuid4().hex[:12]}", f"{PREFIX}{uuid.uuid4().hex[:12]}"
    await _mk(engine, pos, "pending", STALE_MIN)
    await _mk(engine, neg, "acked", STALE_MIN)     # 同样回拨，只有 ack_status 不同

    expected = await _stale_ids(engine)
    got, sent = await _run_probe(set())

    _check_true("1 · 独立直查的判据命中集非空（否则本场景无判别力）", len(expected) >= 1)
    _check_true(f"1 · 正向行 {pos} 被捞到", pos in got)
    _check_true(f"1 · 负对照行 {neg} **未**被捞到", neg not in got)
    _check("1 · probe_once 结果 == 独立直查期望集（两写法互证）", got, expected)
    _check_true("1 · 发了告警", len(sent) == 1)
    _check_true("1 · 告警明细含正向行 payload_id", pos in sent[0][2])
    return pos, neg


async def scenario_2(engine, pos: str, neg: str) -> None:
    """集合不变 ⇒ 静默（防每 60s 刷屏）。"""
    print("\n[场景 2] 集合不变不重发")
    last, _ = await _run_probe(set())          # 先拿当前集合（会发一条，不计）
    _, sent = await _run_probe(last)
    _check("2 · 集合未变 ⇒ 零告警", sent, [])


async def scenario_3(engine, pos: str, neg: str) -> None:
    """恢复：唯一那条正向行 ack 成功 ⇒ 集合清空 ⇒ 发「已恢复」。"""
    print("\n[场景 3] 恢复告警")
    await _set_ack(engine, pos, "acked")
    remaining = await _stale_ids(engine)

    got, sent = await _run_probe({pos})        # last_ids = 上一轮的告警集合

    _check_true(f"3 · 恢复后 {pos} 不再命中", pos not in got)
    _check_true(f"3 · 负对照行始终不命中", neg not in got)
    if remaining:
        print(f"  SKIP  库内另有 {len(remaining)} 条 stale 行（非本探针所造）⇒ "
              "「已恢复」告警本轮无判别力")
        return
    _check_true("3 · 集合清空 ⇒ 发了「已恢复」", any("已恢复" in d for _, _, d in sent))


async def scenario_4(engine) -> None:
    """可重复性：再造一行，**第二轮**仍能命中（防上一轮残留改变结论）。"""
    print("\n[场景 4] 连跑第二轮仍可命中")
    pos2 = f"{PREFIX}{uuid.uuid4().hex[:12]}"
    await _mk(engine, pos2, "blocked", STALE_MIN)   # blocked 也属「回写没成功」

    got, sent = await _run_probe(set())

    _check_true(f"4 · 第二轮新造行 {pos2} 被捞到", pos2 in got)
    _check_true("4 · blocked 不被排除（判据不筛值域）", pos2 in got)
    _check_true("4 · 发了告警", len(sent) == 1)


async def scenario_5(engine) -> None:
    """清理 + 回读：探针不得留残留。"""
    print("\n[场景 5] 清理与回读")
    await _rm_prefix(engine)
    left = await _count_prefix(engine)
    _check("5 · 本探针所造行全部删除（回读）", left, 0)


async def main() -> None:
    logging.basicConfig(level=logging.WARNING)
    engine = create_async_engine(Settings().sqlalchemy_url)   # 不打印（含口令）
    try:
        base = await _count_prefix(engine)
        _check("0 · 开跑前无同名残留（上次探针已清干净）", base, 0)
        pos, neg = await scenario_1(engine)
        await scenario_2(engine, pos, neg)
        await scenario_3(engine, pos, neg)
        await scenario_4(engine)
        await scenario_5(engine)
    finally:
        try:
            await _rm_prefix(engine)     # 兜底清理：中途失败也不留残留
        finally:
            await engine.dispose()
    print(f"\n===== {PASS_COUNT} passed / {FAIL_COUNT} failed =====")
    print("注：本探针只断言 `notify` 被调用；真实 webhook 出站、60s 周期、多 worker 抢锁"
          "均无真机证据。")
    sys.exit(1 if FAIL_COUNT else 0)


if __name__ == "__main__":
    asyncio.run(main())
