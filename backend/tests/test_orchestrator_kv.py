"""P2-9 评测态追溯：_save_result 的 knowledge_version 条件 UPDATE 单测。

mock SessionLocal + _ensure_case_version，直接调 _save_result，验证：
- meta.knowledge_version truthy → 发 UPDATE（json_set 值 + WHERE json_extract IS NULL 首写胜）
- gq 失败空串 "" → 不发 UPDATE（truthy 判定，P1-1）
- meta 缺失/None → 不发 UPDATE
"""
import pytest

from app.runner.executor import CaseOutcome
from app.runner.orchestrator import RunOrchestrator

pytestmark = pytest.mark.asyncio

_KV = "20260824005200"


class _ScalarResult:
    def __init__(self, rows):
        self._rows = rows

    def first(self):
        return self._rows[0] if self._rows else None


class _FakeDB:
    def __init__(self):
        self.updates = []
        self.added = []

    async def execute(self, stmt):
        s = str(stmt).lower()
        if "json_set" in s:                 # UPDATE EvalRun 条件回填
            self.updates.append(str(stmt))
        return _ScalarResult([])            # select EvalResult → 无 existing

    def add(self, obj):
        self.added.append(obj)

    async def commit(self):
        pass


class _FakeSession:
    def __init__(self, db):
        self._db = db

    async def __aenter__(self):
        return self._db

    async def __aexit__(self, *a):
        return False


async def _stub_ensure_case_version(case):
    return 100


def _patch(monkeypatch, fake_db, orch):
    monkeypatch.setattr("app.runner.orchestrator.SessionLocal", lambda: _FakeSession(fake_db))
    monkeypatch.setattr(orch, "_ensure_case_version", _stub_ensure_case_version)


def _outcome(meta=None):
    return CaseOutcome(unified={"meta": meta or {}, "answer": "ok"}, timing={})


async def _save_result(orch, fake_db, outcome):
    # _save_result(run_id, case, outcome)：case 需有 .id（_ensure_case_version 已 mock）
    await orch._save_result(1, type("_C", (), {"id": 101})(), outcome)


async def test_kv_truthy_issues_conditional_update(monkeypatch):
    fake_db = _FakeDB()
    orch = RunOrchestrator()
    _patch(monkeypatch, fake_db, orch)
    await _save_result(orch, fake_db, _outcome({"knowledge_version": _KV}))
    assert len(fake_db.updates) == 1        # 仅发一次（该分支独立于 EvalResult 落库）
    up = fake_db.updates[0].lower()
    assert "json_set" in up                 # 值写 env_snapshot 的 knowledge_version 键
    assert "json_extract" in up and "is null" in up   # WHERE 键 IS NULL → 条件 UPDATE 首写胜
    # 值经绑定参数（:json_set_2）传递，不内联到 SQL 文本——结构已覆盖条件 UPDATE 语义


async def test_kv_empty_string_not_written(monkeypatch):
    # gq 失败回空串 ""：truthy 判定，空串不落库
    fake_db = _FakeDB()
    orch = RunOrchestrator()
    _patch(monkeypatch, fake_db, orch)
    await _save_result(orch, fake_db, _outcome({"knowledge_version": ""}))
    assert fake_db.updates == []


async def test_kv_missing_not_written(monkeypatch):
    fake_db = _FakeDB()
    orch = RunOrchestrator()
    _patch(monkeypatch, fake_db, orch)
    await _save_result(orch, fake_db, _outcome({"model": "gq"}))
    assert fake_db.updates == []


async def test_no_meta_not_written(monkeypatch):
    fake_db = _FakeDB()
    orch = RunOrchestrator()
    _patch(monkeypatch, fake_db, orch)
    await _save_result(orch, fake_db, None)   # outcome=None（error 路径）
    assert fake_db.updates == []
