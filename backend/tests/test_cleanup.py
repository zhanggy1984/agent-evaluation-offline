"""6.4c 数据清理纯函数单测（零 DB 依赖，宿主直接跑）。

只测 candidates_to_purge（保留 N 次 / 排除 pinned / 排序）；
run_cleanup 的 DB 分批删编排 + CASCADE 由容器 e2e 覆盖。
"""
import unittest
from datetime import datetime

from app.models.run import EvalRun as RunModel
from app.runner.cleanup_rules import candidates_to_purge


def _dt(hour):
    return datetime(2026, 8, 1, hour)


def _run(rid, agent_id, suite_id, started_at, pinned=False, version="1.0.0"):
    return RunModel(id=rid, agent_id=agent_id, suite_id=suite_id,
                    started_at=started_at, pinned=pinned, version=version)


class TestCandidatesToPurge(unittest.TestCase):
    def test_retain_keeps_newest_per_group(self):
        """同 (agent, suite) 保留最近 retain 个，删最老。"""
        runs = [_run(1, 1, 10, _dt(1)), _run(2, 1, 10, _dt(2)), _run(3, 1, 10, _dt(3))]
        self.assertEqual(candidates_to_purge(runs,2), [1])

    def test_grouped_by_agent_and_suite(self):
        """不同 agent/suite 独立保留，互不挤占。"""
        runs = [_run(1, 1, 10, _dt(1)), _run(2, 1, 10, _dt(2)),
                _run(3, 1, 11, _dt(3)),
                _run(4, 2, 20, _dt(4)), _run(5, 2, 20, _dt(5))]
        self.assertEqual(candidates_to_purge(runs,1), [1, 4])

    def test_pinned_excluded(self):
        """pinned（关键版本）永不清理，且不挤占保留名额。"""
        runs = [_run(1, 1, 10, _dt(1), pinned=True), _run(2, 1, 10, _dt(2)),
                _run(3, 1, 10, _dt(3))]
        # pinned 排除后剩 2 个 = retain，无候选
        self.assertEqual(candidates_to_purge(runs,2), [])
        # pinned 排除后剩 2 个，retain=1 → 只删最老的非 pinned（run2）
        self.assertEqual(candidates_to_purge(runs,1), [2])

    def test_below_retain_no_candidates(self):
        self.assertEqual(candidates_to_purge(
            [_run(1, 1, 10, _dt(1)), _run(2, 1, 10, _dt(2))],5), [])

    def test_oldest_first_sorted(self):
        """候选按 id 升序（分批删除优先清最老数据）。"""
        runs = [_run(9, 1, 10, _dt(9)), _run(3, 1, 10, _dt(3)), _run(7, 1, 10, _dt(7))]
        self.assertEqual(candidates_to_purge(runs,0), [3, 7, 9])

    def test_empty_input(self):
        self.assertEqual(candidates_to_purge([],50), [])

    def test_version_representative_protected(self):
        """每版本最近 1 条优先保护：retain=2 时保留 v1/v2/v3 代表，删 v3 旧 run 与 v1 旧 run。"""
        runs = [
            _run(1, 1, 10, _dt(1), version="1.0.0"),
            _run(2, 1, 10, _dt(2), version="1.0.0"),   # v1 代表（最新）
            _run(3, 1, 10, _dt(3), version="2.0.0"),   # v2 代表
            _run(4, 1, 10, _dt(4), version="3.0.0"),
            _run(5, 1, 10, _dt(5), version="3.0.0"),   # v3 代表（最新）
        ]
        # protected=[run5, run3, run2]（按时间倒序）→ retain=2 keep=[5,3]；purge 其余
        self.assertEqual(candidates_to_purge(runs,2), [1, 2, 4])

    def test_version_more_than_retain_drops_oldest_version(self):
        """版本数 > retain 时从最老版本代表开始淘汰（总量仍受 retain 限制）。"""
        runs = [
            _run(1, 1, 10, _dt(1), version="1.0.0"),
            _run(2, 1, 10, _dt(2), version="2.0.0"),
            _run(3, 1, 10, _dt(3), version="3.0.0"),
        ]
        # protected=[run3, run2, run1] → retain=2 keep=[3,2]；v1 代表被淘汰
        self.assertEqual(candidates_to_purge(runs,2), [1])


if __name__ == "__main__":
    unittest.main()
