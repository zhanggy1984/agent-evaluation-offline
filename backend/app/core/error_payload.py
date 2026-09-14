"""回流信封：结构自检 / 词表净化 / 映射（P1 §2.1、SD §5.3/§5.4）。

`validate_envelope` 与 `sanitize_words` 是**无 IO 纯函数**（可单测穷举）；
`resolve_agent` / `resolve_interface` 要查库，是同文件内的 async 例外（SD §5.4 就放在这里）。

**大小写归一：已落地（#235，2026-09-14 拍板）——本条此前是「有意偏离」，现为翻案记录。**

历史：本实现原先**不做**归一，理由是净化后的 keywords 会被 `KeywordNotContainsOp` 以
`k in val` **大小写敏感**地匹配原始 answer，只把词表降为小写等于让「Fallback」这类带大写的
兜底话术**匹配不上** ⇒ 静默漏判。该推理当时成立，**结论在今天不再成立**——因为算子侧已
同步改为对两侧 `lower()`（`assertions/ops/text.py` 的 `_keyword_hits`）。两半必须同批动：
**只改词表 = 漏判，只改算子 = 词表未折叠（去重与呈现不一致）**。

⚠️ 代价（承认，未闭环）：折叠扩大了**短 ASCII 关键词的误命中面**（`"AI"` 命中 `"he said"`），
对 `keyword_contains` 是假绿方向。实际发生率**待量化**，本批未加长度闸。
"""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.agent import Agent, AgentInterface

SCHEMA_VERSION = "1.0"
CASE_TYPE = "regression_error"

# 单条兜底话术的长度上限。文档未钉死具体数值，取 200（远超自然话术，只为挡脏数据/攻击性超长串）
MAX_WORD_LEN = 200


# ---------- 结构自检（纯函数） ----------


def _dig(envelope: dict, dotted: str):
    """按点号路径取值；任一段缺失/中途非 dict → None（不抛）。"""
    cur = envelope
    for part in dotted.split("."):
        if not isinstance(cur, dict):
            return None
        cur = cur.get(part)
    return cur


def _is_blank(v) -> bool:
    """「缺」与「空」都算不满足必填——空串、纯空白、空容器一律视为未提供。"""
    if v is None:
        return True
    if isinstance(v, str):
        return not v.strip()
    if isinstance(v, (list, dict)):
        return len(v) == 0
    return False


# 必填（缺即驳回 content_gap）；顺序即报错顺序，保证 detail 稳定可对比
_REQUIRED = (
    "schema_version",
    "case_type",
    "payload_id",
    "source.agent",
    "source.interface",
    "source.trace_id",
    "versions.trigger_version",
    "evidence.input",
    "assert.no_fallback.config_ref.wordlist_version",
    "no_fallback_config",
)


def validate_envelope(envelope: dict) -> tuple[bool, list[str], str]:
    """结构自检。返回 `(ok, errors, verdict)`，verdict ∈ ok / content_gap / version_drift。

    **verdict 只有三个值**：`version_drift` 表示「本平台不认识这份载荷」（schema 或 case_type
    越界）——requeue 不愈，须升级/联调；`content_gap` 表示「载荷内容缺/畸形」——online admin
    requeue 可愈。两者的 reject_code 相同但处置路径不同，故必须分开。
    """
    if not isinstance(envelope, dict):
        return False, ["信封不是 JSON 对象"], "content_gap"

    errors = [f"缺必填项：{p}" for p in _REQUIRED if _is_blank(_dig(envelope, p))]

    # --- 版本域硬校验：**只在值已提供时**才判 version_drift ---
    # schema_version / case_type 同时出现在「必填表」与「硬校验表」里，二者不冲突：
    #   缺 / 空 → 走上面的必填 → content_gap（内容缺，online admin requeue 可愈）
    #   给了但不认识 → version_drift（本平台不认识这份载荷，requeue 不愈，须升级/联调）
    # 合成一个分支就会把「缺字段」误报成「版本不识别」，把可自愈的故障指成需人工升级。
    schema = envelope.get("schema_version")
    if not _is_blank(schema) and schema != SCHEMA_VERSION:
        return False, [f"schema_version 不支持：{schema!r}（当前 {SCHEMA_VERSION}）"], "version_drift"
    case_type = envelope.get("case_type")
    if not _is_blank(case_type) and case_type != CASE_TYPE:
        return False, [f"case_type 不在白名单：{case_type!r}（当前 {CASE_TYPE}）"], "version_drift"

    # --- 词表：fail-closed（空/缺/非法版本一律不过，绝不静默 pass）---
    nfc = envelope.get("no_fallback_config")
    if _is_blank(nfc):
        # 缺失/空 → 已由上面的必填校验记错，此处不重复报
        pass
    elif not isinstance(nfc, dict):
        # fail-closed：**非空且非对象**的形态（`"x"` / `["a"]` / `7`）原先被 isinstance
        # 短路、整块跳过 ⇒ 必填校验又因「非空」放行 ⇒ ok=True。畸形载荷被当合法收下，
        # 与 §5.3「空词表/words 缺失/版本非法 → content_gap，不静默 pass」相反。
        errors.append(
            f"no_fallback_config 形态非法：期望对象，实为 {type(nfc).__name__}"
        )
    else:
        words = nfc.get("words")
        if not isinstance(words, list) or not words:
            errors.append("no_fallback_config.words 为空或非数组（fail-closed：空词表不判 pass）")
        ver = nfc.get("wordlist_version")
        if not isinstance(ver, int) or isinstance(ver, bool) or ver < 0:
            errors.append(f"no_fallback_config.wordlist_version 非法：{ver!r}")
        # 同源同值：assert 区固化的版本必须与 no_fallback_config 一致，否则两份词表可能不同代
        ref_ver = _dig(envelope, "assert.no_fallback.config_ref.wordlist_version")
        if not _is_blank(ref_ver) and ref_ver != ver:
            errors.append(
                f"词表版本不同源：assert.config_ref={ref_ver!r} vs no_fallback_config={ver!r}"
            )

    return (not errors), errors, ("ok" if not errors else "content_gap")


# ---------- 词表净化（纯函数） ----------


def sanitize_words(raw) -> list[str]:
    """词级净化：strip → **大小写归一** → 弃空/纯空白 → 弃超长 → **保序去重**。

    归一是 #235（2026-09-14 拍板，**翻案**：此前本条明确「不做归一」，理由见文件头）
    的一半。这里折叠的作用**不是匹配**（匹配由算子侧 `_keyword_hits` 对两侧 lower 兜住），
    而是：① **去重**——`["Fallback","fallback"]` 折叠后并为一个，`len(keywords)` 才与
    实际不同检查数一致（`KeywordNotContainsOp` 的 `match="any"` 用 `len(keywords)` 算，
    有重复项会算错）；② **呈现一致**——审计/后台看到的词表与判定口径同形。

    返回的列表即 case.assertions 里 keywords 的值。
    """
    if not isinstance(raw, list):
        return []
    out: list[str] = []
    seen: set[str] = set()
    for item in raw:
        if not isinstance(item, str):
            continue
        w = item.strip().lower()
        if not w or len(w) > MAX_WORD_LEN or w in seen:
            continue
        seen.add(w)
        out.append(w)
    return out


# ---------- 映射（查库） ----------


async def resolve_agent(session: AsyncSession, source_agent: str) -> Agent | None:
    """`Agent.name` 精确匹配（Q2 已砍别名）。不命中 → None → 调用方按 offline_cap_gap 驳回。"""
    if not source_agent:
        return None
    return (
        await session.execute(select(Agent).where(Agent.name == source_agent))
    ).scalar_one_or_none()


async def resolve_interface(
    session: AsyncSession, agent: Agent, method: str, path: str
) -> AgentInterface | None:
    """按 method + path 逐段匹配；`{...}` 占位段当通配、其余段精确。

    入参 `path` 允许带前导 `"METHOD "` 前缀（online 侧 interface 串的形态），此处剥掉。
    不命中 → None → offline_cap_gap。
    """
    method = (method or "").strip().upper()
    path = (path or "").strip()
    if " " in path:  # "GET /a/b" → 剥前缀
        head, _, rest = path.partition(" ")
        if head.isupper() and rest.startswith("/"):
            method = method or head
            path = rest
    if not method or not path:
        return None

    rows = (
        await session.execute(
            select(AgentInterface).where(
                AgentInterface.agent_id == agent.id, AgentInterface.method == method
            )
        )
    ).scalars().all()
    want = [s for s in path.split("/") if s]
    for row in rows:
        got = [s for s in (row.path or "").split("/") if s]
        if len(got) != len(want):
            continue
        if all(
            (g.startswith("{") and g.endswith("}")) or g == w for g, w in zip(got, want)
        ):
            return row
    return None
