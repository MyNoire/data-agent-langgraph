"""导出不含模型服务连接地址的可公开评测结果。

完整本地结果保留在 Git 忽略的 ``results/`` 路径中；公开结果保留逐题
SQL 和匹配标记，便于复核失败案例。
"""

import argparse
import json
from pathlib import Path


TOP_LEVEL = (
    "dataset_sha256", "provider", "status", "run_id", "summary",
    "evidence_mode", "model",
    "model_temperature", "prompt_version", "graph_version", "count", "max_rows",
    "query_timeout_ms", "max_response_bytes", "valid_sql_rate", "execution_accuracy",
    "p50_latency_ms", "p95_latency_ms",
)
RECORD = (
    "index", "question_id", "database_id", "graph_run_id", "evidence_used", "predicted_sql",
    "valid_sql", "execution_match", "outcome", "latency_ms",
)


def main() -> None:
    """读取原始评测 JSON 并按字段白名单导出公开副本。

    参数:
        无函数参数；从命令行读取输入和输出文件路径。
    返回:
        None；写入去掉服务地址等字段的 JSON 并打印输出路径。
    异常:
        KeyError: 原始结果缺少 records 字段。
        OSError: 输入读取或输出写入失败。
    """
    parser = argparse.ArgumentParser()
    parser.add_argument("input", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    raw = json.loads(args.input.read_text(encoding="utf-8"))
    public = {key: raw[key] for key in TOP_LEVEL if key in raw}
    public["records"] = [
        {key: record[key] for key in RECORD if key in record}
        for record in raw["records"]
    ]
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(public, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(args.output)


if __name__ == "__main__":
    main()
