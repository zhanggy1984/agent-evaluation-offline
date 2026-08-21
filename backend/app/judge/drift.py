"""6.4 元评测：judge 漂移一致性纯函数（零 DB 依赖，宿主单测直接跑）。

与 dashboard_rules 同模式：宿主单测只 import 本模块，不触 app.core.db。
口径：judge score 步进 20（RATINGS × 100），容差 10 → 重判等级必须与金标准等级
严格一致才计「一致」（level 差 1 → score 差 20 > 10）。
"""
from __future__ import annotations

# 一致容差：|重判分 − gold分| ≤ 10 计一致（等级严格一致）
DRIFT_SCORE_TOLERANCE = 10.0


def dim_consistency(pairs: list[tuple[float, float]],
                    tolerance: float = DRIFT_SCORE_TOLERANCE) -> float:
    """单维度一致率：一致对数 / 总对数。空输入 → 0.0（无可比判据，视同不达标）。"""
    if not pairs:
        return 0.0
    agree = sum(1 for re, gold in pairs if abs(re - gold) <= tolerance)
    return round(agree / len(pairs), 4)


def is_drift(rate: float | None, threshold: float) -> bool:
    """漂移判定：一致率低于阈值 → 漂移。rate 缺失（无可比判据）同样视为漂移（需人关注）。"""
    return rate is None or rate < threshold
