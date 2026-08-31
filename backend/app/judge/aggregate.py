"""judge 多次采样聚合（P0-1：档位多数决对冲 LLM 采样噪声）。

背景：judge 对同一 (case, 维度) 判 repeat 次（worker._process_one），单次判定
temperature=0 仍受服务端采样噪声影响（gq 3182：同一回答 3 次 = fail/pass/pass）。
本模块把多次 verdict 聚合为单档结果，供 scorer 消费。

聚合策略（用户确认，2026-08-31）：
- 档位多数决：取出现次数最多的 level 档；多档并列时**显式取较低档**（保守）
  ——不能依赖 collections.Counter.most_common() 的插入序（并列时按插入序返回，
  repeat=2 的平局会因采样到达顺序非确定翻向高档，门禁 value<target 随机翻转）。
- score = RATINGS[majority] × 100
- reason = 第一个多数档样本的 reason（不拼接——拼接会把离群档的"有缺陷"理由挂在
  多数档分数旁，evidence 自相矛盾）；离群样本 reason 保留在 repeats[i].reason 供下钻。
- 输出顶层字段与旧单 verdict 结构兼容（scorer 只读 dimension/score/reason/rubric_version），
  新增 repeat（=实际采样数，可识别退化多数决）+ repeats（各次明细）。

顶层零 DB 依赖（纯函数），宿主单测直接测。
"""
from __future__ import annotations

from collections import Counter
from typing import Any

from app.judge.client import JudgeVerdict
from app.judge.rubric import RATINGS


def aggregate_verdicts(verdicts: list[JudgeVerdict]) -> dict[str, Any] | None:
    """把多次判分聚合为档位多数决结果；空输入（无成功采样）返回 None。

    入参 verdicts 必须来自同一 (case, 维度)，dimension/rubric_version 取首个样本。
    """
    if not verdicts:
        return None
    counts = Counter(v.level for v in verdicts)
    max_count = max(counts.values())
    majority = min(level for level, c in counts.items() if c == max_count)  # tie 显式取低档

    # 第一个多数档样本：其 reason 代表判定依据（离群样本 reason 在 repeats 中下钻）
    anchor = next(v for v in verdicts if v.level == majority)
    return {
        "dimension": verdicts[0].dimension,
        "level": majority,
        "score": RATINGS[majority] * 100,
        "reason": anchor.reason,
        "rubric_version": verdicts[0].rubric_version,
        "repeat": len(verdicts),  # 实际采样数（部分失败 < 配置 repeat，evidence 可识别）
        "repeats": [
            {"level": v.level, "score": RATINGS[v.level] * 100, "reason": v.reason}
            for v in verdicts
        ],
    }
