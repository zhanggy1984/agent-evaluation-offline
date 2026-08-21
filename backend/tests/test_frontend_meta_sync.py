"""#230 收尾：前端静态维护表与后端契约同步守卫（宿主/容器均可跑，纯文件对比无 DB）。

背景：configMeta.js（前端配置中心中文说明）与 seed.py DEFAULT_SYSTEM_CONFIG 双处维护，
漏同步时前端兜底显示原 key 无中文说明（静默降级不报错）。此处双向严格相等校验兜底。

不用 `from app.seed import DEFAULT_SYSTEM_CONFIG`——seed.py 顶层 import security，宿主无法 import
（见 test_seed.py 模块注释）。故用正则从源文件提取 key：configMeta.js 顶级 key 恰 2 空格缩进、
seed.py DEFAULT_SYSTEM_CONFIG 内 key 恰 4 空格缩进双引号，格式变更时测试报错即提示人工核对。
"""
import re
from pathlib import Path

BACKEND_DIR = Path(__file__).parents[1]
ROOT_DIR = BACKEND_DIR.parent

CONFIG_META_PATH = ROOT_DIR / "frontend" / "src" / "constants" / "configMeta.js"
SEED_PATH = BACKEND_DIR / "app" / "seed.py"


def _config_meta_keys() -> set[str]:
    text = CONFIG_META_PATH.read_text(encoding="utf-8")
    return set(re.findall(r'^ {2}[\'"]?([A-Za-z][\w.]*)[\'"]?\s*:', text, re.M))


def _seed_config_keys() -> set[str]:
    text = SEED_PATH.read_text(encoding="utf-8")
    block = re.search(r"DEFAULT_SYSTEM_CONFIG = \{(.*?)\n\}", text, re.S)
    assert block, "seed.py 中找不到 DEFAULT_SYSTEM_CONFIG 块"
    return set(re.findall(r'^ {4}"([\w.]+)":', block.group(1), re.M))


def test_config_meta_keys_match_seed():
    """configMeta.js 与 DEFAULT_SYSTEM_CONFIG 的 key 集合双向严格相等。"""
    meta = _config_meta_keys()
    seed = _seed_config_keys()
    assert meta == seed, (
        f"configMeta.js 与 seed.py DEFAULT_SYSTEM_CONFIG 不同步："
        f"seed 有但 configMeta 缺 {sorted(seed - meta)}；"
        f"configMeta 有但 seed 缺 {sorted(meta - seed)}"
    )
