"""B5：导出一次性 token + 过期文件惰性清理。

- used=True 的 token 二次下载 → 400。并发下两个请求同 token：with_for_update 锁定读串行化，
  第二个醒来读到 used=True 恰命中此分支 → 一次性语义防绕过（锁的并发行为由真库保证，
  单测验证该分支本身；与 B6 refresh 并发 integration 同思路）
- EXPORT_DIR 中 mtime 超过 TTL 的文件随 create/download 惰性清理，新文件保留
- 正常下载：used 置 True + FileResponse
"""
import os
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.api import exports as exp
from app.api.exports import EXPORT_TTL_HOURS, _cleanup_stale_exports, download_export
from app.core.errors import ApiError


def _req():
    return SimpleNamespace(headers={}, client=SimpleNamespace(host="10.0.0.1"))


class TestDownloadOnce:
    @pytest.mark.asyncio
    async def test_used_token_400(self):
        # 已使用 token 二次下载 → 400（并发第二个请求经 FOR UPDATE 读到 used=True 也走此分支）
        row = SimpleNamespace(
            token_hash="h", run_id=1, format="pdf", user_id=7,
            expires_at=None, used=True,
        )
        db = AsyncMock()
        db.execute.return_value = SimpleNamespace(
            scalars=lambda: SimpleNamespace(first=lambda: row))
        user = SimpleNamespace(id=7, role="evaluator")
        with pytest.raises(ApiError) as ei:
            await download_export("tok", _req(), db, user)
        assert ei.value.status_code == 400
        assert "已被使用" in ei.value.message

    @pytest.mark.asyncio
    async def test_normal_download_marks_used(self, tmp_path, monkeypatch):
        # 未用 + 未过期 + 同用户 → FileResponse，且 used 置 True（一次性标记）
        from datetime import datetime, timedelta

        monkeypatch.setattr(exp, "EXPORT_DIR", tmp_path)
        (tmp_path / "h.pdf").write_bytes(b"%PDF-1.4")
        row = SimpleNamespace(
            token_hash="h", run_id=1, format="pdf", user_id=7,
            expires_at=datetime.now() + timedelta(hours=1), used=False,
        )
        db = AsyncMock()
        db.execute.return_value = SimpleNamespace(
            scalars=lambda: SimpleNamespace(first=lambda: row))
        db.add.side_effect = lambda *a, **k: None  # write_audit 只 db.add，普通方法即可
        user = SimpleNamespace(id=7, role="evaluator", username="u7")
        resp = await download_export("tok", _req(), db, user)
        assert resp.status_code == 200
        assert row.used is True


class TestCleanup:
    def test_stale_file_removed_fresh_kept(self, tmp_path, monkeypatch):
        monkeypatch.setattr(exp, "EXPORT_DIR", tmp_path)
        old = tmp_path / "a.pdf"
        fresh = tmp_path / "b.pdf"
        old.write_bytes(b"x")
        fresh.write_bytes(b"x")
        past = time.time() - EXPORT_TTL_HOURS * 3600 - 10
        os.utime(old, (past, past))
        _cleanup_stale_exports()
        assert not old.exists(), "超过 TTL 的文件应被惰性清理"
        assert fresh.exists(), "未过期文件应保留"

    def test_cleanup_missing_dir_noop(self, tmp_path, monkeypatch):
        monkeypatch.setattr(exp, "EXPORT_DIR", tmp_path / "nope")
        _cleanup_stale_exports()  # 目录不存在 → 直接 return，不抛
