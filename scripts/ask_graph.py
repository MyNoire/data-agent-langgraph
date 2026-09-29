"""通过第一条线性 LangGraph 工作流回答一个问题。"""
import argparse
import json
import logging
import time
from pathlib import Path
from uuid import uuid4

from querypilot.config import get_settings
from querypilot.graph.single import build_single_query_graph
from querypilot.logging import progress_logging


def main() -> None:
    """解析命令行参数、调用图并打印 JSON 结果。

    参数:
        无函数参数；从 --database-id 和 --question 读取输入。
    返回:
        None；进度写入标准错误与 JSONL 文件，最终 QueryOutput 写到标准输出。
    异常:
        数据库未知、模型失败或 SQL 被拒绝时，由底层异常终止命令。
    """
    parser = argparse.ArgumentParser()
    parser.add_argument("--database-id", required=True)
    parser.add_argument("--question", required=True)
    args = parser.parse_args()
    graph = build_single_query_graph(get_settings())
    run_id = uuid4().hex
    events = Path("results") / f"graph__{run_id}.events.jsonl"
    with progress_logging(events):
        progress = logging.getLogger("querypilot.progress")
        started = time.monotonic()
        fields = {"run_id": run_id, "component": "graph", "database_id": args.database_id}
        progress.info("graph_started", extra={**fields, "log_file": str(events)})
        try:
            answer: dict[str, object] = {}
            for part in graph.stream(
                    {
                        "database_id": args.database_id,
                        "question": args.question,
                        "run_id": run_id,
                    },
                    stream_mode="updates",
                    version="v2",
            ):
                if part["type"] != "updates":
                    continue
                for delta in part["data"].values():
                    if not isinstance(delta, dict):
                        continue
                    if "sql" in delta:
                        answer["sql"] = delta["sql"]
                    if "result" in delta:
                        answer["result"] = delta["result"]

            if set(answer) != {"sql", "result"}:
                raise RuntimeError("graph stream ended without a complete answer")
        except Exception as exc:
            progress.warning("graph_finished", extra={
                **fields, "status": "error", "error_type": type(exc).__name__,
                "elapsed_ms": round((time.monotonic() - started) * 1000, 3),
            })
            raise
        progress.info("graph_finished", extra={
            **fields, "status": "success",
            "elapsed_ms": round((time.monotonic() - started) * 1000, 3),
        })
    print(json.dumps(answer, indent=2, ensure_ascii=False, default=str))


if __name__ == "__main__":
    main()
