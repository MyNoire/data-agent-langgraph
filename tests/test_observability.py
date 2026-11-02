"""验证新建独立输出端的实际收益：并发运行不会把日志写进对方文件。"""
import json
import logging
from concurrent.futures import ThreadPoolExecutor
from io import StringIO
from threading import Barrier
from typing import TypedDict

from langgraph.graph import START, END, StateGraph

from querypilot.runtime.api import graph_logging


def test_concurrent_log_sessions_keep_files_separate(tmp_path):
    """验证两个真实重叠的调用拥有独立文件和业务 ID。

    参数：tmp_path 为隔离临时目录。
    返回：None；检查文件隔离、事件完整以及全局 logger 未被修改。
    """
    class State(TypedDict):
        """本实验只需要一个整数状态。"""
        value: int

    barrier = Barrier(2)
    shared_logger = logging.getLogger("querypilot.progress")
    old_handlers = shared_logger.handlers[:]

    def work(state):
        """参数：state 含 value。返回：增量；屏障确保两个调用确实并行。"""
        barrier.wait(timeout=10)
        return {"value": state["value"] + 1}

    builder = StateGraph(State)
    builder.add_node("work", work).add_edge(START, "work").add_edge("work", END)
    graph = builder.compile(name="parallel_probe")

    def run(run_id):
        """参数：run_id 为业务标识。返回：独立日志文件路径。"""
        path = tmp_path / f"{run_id}.jsonl"
        with graph_logging(path, stream=StringIO()) as logs:
            answer = graph.invoke({"value": 1}, config=logs.config(run_id=run_id, database_id="demo"))
            assert answer["value"] == 2
        return path

    with ThreadPoolExecutor(max_workers=2) as pool:
        paths = list(pool.map(run, ["run-a", "run-b"]))
    for path in paths:
        events = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
        assert len(events) == 4
        assert {event["run_id"] for event in events} == {path.stem}
        starts = {e["span_id"] for e in events if e["event"] == "graph_span_started"}
        ends = {e["span_id"] for e in events if e["event"] == "graph_span_finished"}
        assert starts == ends
    assert shared_logger.handlers == old_handlers
