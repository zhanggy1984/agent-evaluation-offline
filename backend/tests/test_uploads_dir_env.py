"""P2-E2 UPLOADS_DIR 口径统一单测：uploads 写入目录与 base 路径白名单同源。

原 uploads.py 硬编码 Path("/app/uploads")，而 adapters/base.py 读 UPLOADS_DIR env——
env 一改，白名单走新路径、写入仍写老路径，上传全被白名单拒绝。现在两处都读
os.path.realpath(os.environ.get("UPLOADS_DIR", "/app/uploads"))，默认 env 下同值。
若未来一处改 env 一处改硬编码，本测试会断。
"""
from app.adapters.base import _UPLOADS_DIR
from app.api.uploads import UPLOAD_DIR


def test_uploads_dir_same_source_as_base_whitelist():
    assert str(UPLOAD_DIR) == _UPLOADS_DIR
