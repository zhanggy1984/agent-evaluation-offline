"""B.5 e2e：平台暂无 suite/case 创建 API，测试直插 DB 造一个 active case。

用法：python -m tests.seed_probe_case <agent_id> <interface_id> [文件路径]
带第三个参数时造文件型用例（input_type=file，input.file_path 指向平台容器内路径，
供 adapter_config.files 模板引用）；否则造文本型闲聊用例。
输出：新建 suite.id（stdout 最后一行；aiomysql 关闭噪音在 stderr）。
"""
import asyncio
import sys

from app.core.db import SessionLocal
from app.models import TestCase, TestSuite


async def main() -> None:
    agent_id, interface_id = int(sys.argv[1]), int(sys.argv[2])
    file_path = sys.argv[3] if len(sys.argv) > 3 else None
    async with SessionLocal() as db:
        suite = TestSuite(agent_id=agent_id, name="B.5 probe-e2e")
        db.add(suite)
        await db.flush()
        if file_path:
            # 决策 #12：文件型用例引用平台自存文件；input.file_path 供模板 {case.input.file_path}
            case_input = {"file_path": file_path}
            input_type = "file"
            name = "probe-file-case"
        else:
            case_input = {"content": "你好，请介绍一下你自己"}
            input_type = "text"
            name = "probe-case"
        db.add(TestCase(
            suite_id=suite.id, interface_id=interface_id, name=name,
            input_type=input_type, input=case_input,
            file_ref=file_path,   # 平台自存文件引用
            expected={}, assertions={}, metrics={}, status="active",
        ))
        await db.commit()
        print(suite.id)


if __name__ == "__main__":
    asyncio.run(main())
