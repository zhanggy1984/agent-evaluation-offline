"""LLM-judge 分级 rubric（§7.2 / §15.2 / 决策 #38）。

六级锚点 0.0/0.2/0.4/0.6/0.8/1.0，每语义维度一套（factuality/reasoning_quality）。
- 模板存 judge_rubric 表（interface_id=0 通用，非 0 按接口覆盖），seed 幂等插入
- 未配置 → 回退内置模板（fallback_rubric）
- judge 输出只给 level(0-5 锚点索引) + reason，不输出连续分

顶层零 DB 依赖（BUILTIN_RUBRICS/RATINGS 纯数据），宿主单测可直接测。
"""
from __future__ import annotations

import logging

from sqlalchemy import select

logger = logging.getLogger(__name__)

# 六级锚点：索引 0-5 → score = RATINGS[level] × 100
RATINGS = (0.0, 0.2, 0.4, 0.6, 0.8, 1.0)

# 内置模板（seed 与运行时回退共用）。template 结构：
#   {dimension, name, instruction, anchors[{level,label,desc}×6], output_schema}
BUILTIN_RUBRICS: dict[str, dict] = {
    "factuality": {
        "version": "1.2",
        "template": {
            "dimension": "factuality",
            "name": "事实性",
            "instruction": (
                "评估 agent 回答的事实准确性：是否与参考依据文档（若有）及黄金答案一致，"
                "有无凭空编造、数字错误、张冠李戴、信息张冠李戴。"
                "参考依据文档是事实基准，agent 回答中与参考依据一致的内容视为忠实引用；"
                "仅当内容与参考依据冲突、或明显凭空编造参考依据中不存在的信息时才扣分。"
                "区分事实断言与缺口提示：agent 指出参考依据中未明确或未覆盖的内容"
                "（如「标书未给出 RTO 数值」「未提供 SLA 指标」）属合理的评审缺口提示，不算编造；"
                "编造仅指 agent 将参考依据中不存在的信息当作事实陈述（如声称「RTO 为 4 小时」）。"
                "只依据提供的待评数据判断，不要因表达风格、篇幅长短扣分。"
            ),
            "anchors": [
                {"level": 0, "label": "完全错误", "desc": "回答与事实根本冲突，核心内容编造或无中生有"},
                {"level": 1, "label": "严重偏差", "desc": "关键事实错误，或编造了待评数据中不存在的具体信息"},
                {"level": 2, "label": "明显偏差", "desc": "部分关键事实错误，或关键数字/名称/对象不准确"},
                {"level": 3, "label": "部分准确", "desc": "主体事实正确，但存在个别细节错误或表述含糊"},
                {"level": 4, "label": "基本准确", "desc": "事实准确，仅轻微不精确或省略次要细节"},
                {"level": 5, "label": "完全准确", "desc": "与事实/黄金答案完全一致，无任何错误"},
            ],
            "output_schema": {
                "level": "整数 0-5，对应上述 anchors 的 level",
                "reason": "引用待评数据中的具体证据说明判级理由（100 字内）",
            },
        },
    },
    "reasoning_quality": {
        "version": "1.1",
        "template": {
            "dimension": "reasoning_quality",
            "name": "思考链",
            "instruction": (
                "评估 agent 思考链的推理质量：逻辑是否连贯、因果是否成立、"
                "结论是否由前提正确推导、有无关键步骤遗漏或自相矛盾。"
                "只评推理过程，不评事实内容本身（事实性另维度评估）。"
                "基于参考依据的合理推断、简洁的表述、未展开次要细节，均属正常表达，不构成瑕疵；"
                "仅在存在逻辑断裂、结论无法由前提推出、自相矛盾、关键推理步骤缺失时才降级，"
                "不要因表达风格或篇幅长短扣分。"
            ),
            "anchors": [
                {"level": 0, "label": "完全混乱", "desc": "推理前后矛盾，结论与推导无关"},
                {"level": 1, "label": "严重缺陷", "desc": "关键推理步骤缺失或逻辑断裂，结论不可由前提推出"},
                {"level": 2, "label": "明显缺陷", "desc": "部分步骤逻辑错误，或忽略了对结论至关重要的因素"},
                {"level": 3, "label": "部分合理", "desc": "主干推理成立，但个别步骤跳跃或论证不充分"},
                {"level": 4, "label": "基本合理", "desc": "推理连贯合理，仅有轻微瑕疵或省略"},
                {"level": 5, "label": "完整严密", "desc": "推理链条完整、逻辑严密，无缺陷"},
            ],
            "output_schema": {
                "level": "整数 0-5，对应上述 anchors 的 level",
                "reason": "引用待评数据中的具体证据说明判级理由（100 字内）",
            },
        },
    },
}

# 内置模板的版本号（seed 幂等 key 之一：dimension_code+interface_id=0+version）
BUILTIN_RUBRIC_VERSION = "1.2"


def fallback_rubric(dimension_code: str) -> dict | None:
    """内置模板回退（DB 未配置时）。未知维度返回 None。"""
    entry = BUILTIN_RUBRICS.get(dimension_code)
    return entry["template"] if entry else None


def anchors_of(template: dict) -> list[dict]:
    """anchors 列表（按 level 升序）。"""
    return sorted(template.get("anchors", []), key=lambda a: a["level"])


async def load_rubric(db, dimension_code: str, interface_id: int = 0) -> dict:
    """读 judge_rubric：interface 覆盖 > 通用 0，取最新版本；无 → 内置回退。

    interface_id=0 时只看通用；非 0 时 interface 优先、通用兜底。
    """
    from app.models import JudgeRubric

    if interface_id:
        rows = (await db.execute(select(JudgeRubric).where(
            JudgeRubric.dimension_code == dimension_code,
            JudgeRubric.interface_id.in_((0, interface_id)),
        ).order_by(JudgeRubric.interface_id.desc(), JudgeRubric.id.desc()))).scalars().all()
    else:
        rows = (await db.execute(select(JudgeRubric).where(
            JudgeRubric.dimension_code == dimension_code,
            JudgeRubric.interface_id == 0,
        ).order_by(JudgeRubric.id.desc()))).scalars().all()
    if rows:
        return rows[0].template
    tpl = fallback_rubric(dimension_code)
    if tpl is None:
        raise ValueError(f"维度 {dimension_code} 无 rubric（内置也未注册）")
    logger.warning("judge_rubric 未配置 %s，使用内置模板", dimension_code)
    return tpl
