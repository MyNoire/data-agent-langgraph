"""通过第一条线性 LangGraph 工作流回答一个问题。"""
import argparse
import json
import logging
import time
from pathlib import Path
from uuid import uuid4

from querypilot.config import get_settings
from querypilot.graph.single import build_single_query_graph, query_result_row_count
from querypilot.observability import graph_logging


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
    with graph_logging(events, row_count=query_result_row_count) as logs:
        result = graph.invoke(
            {"database_id": args.database_id, "question": args.question},
            config=logs.config(run_id=run_id, database_id=args.database_id),
        )
    print(json.dumps(result, indent=2, ensure_ascii=False, default=str))


if __name__ == "__main__":
    main()
