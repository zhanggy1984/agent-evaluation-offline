"""asyncio.run 的独立 event loop 替代（D15 教训统一收口）。

背景：pytest-asyncio 配置 default_loop_scope=session，全量共享一个 session loop。
asyncio.run() 结束时调用 set_event_loop(None)，把 asyncio policy 的当前 loop 置空，
其后所有 async 测试获取 session loop 时抛
RuntimeError: There is no current event loop in thread 'MainThread'（见上线待办 E2E 段）。

本函数在独立 loop 上运行协程，执行与 asyncio.run 等价的清理（取消遗留任务、
关闭 async generator 与默认 executor），但绝不 set_event_loop：
asyncio policy 的当前 loop 零触碰，共享 session loop 不受影响。

sync 测试需要跑协程时一律用 run_in_isolated_loop，禁止裸 asyncio.run。
"""
import asyncio


def run_in_isolated_loop(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        try:
            _cancel_all_tasks(loop)
            loop.run_until_complete(loop.shutdown_asyncgens())
            loop.run_until_complete(loop.shutdown_default_executor())
        finally:
            loop.close()


def _cancel_all_tasks(loop):
    """取消 loop 上所有未完成任务（对齐 asyncio.run 的清理语义，防御超时遗留任务）。"""
    to_cancel = asyncio.all_tasks(loop)
    if not to_cancel:
        return
    for task in to_cancel:
        task.cancel()
    loop.run_until_complete(asyncio.gather(*to_cancel, return_exceptions=True))
    for task in to_cancel:
        if task.cancelled():
            continue
        exc = task.exception()
        if exc is not None:
            loop.call_exception_handler({
                "message": "unhandled exception during run_in_isolated_loop shutdown",
                "exception": exc,
                "task": task,
            })
